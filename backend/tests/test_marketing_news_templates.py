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
  violation; JSONB round trips → []), and the name-free alt text;
* news-v19, Money Map (Batch 1 plan item 2): the neutral "How fiscal {fy} revenue broke down" and "by
  region" only when every named part is on `REGION_NAMES` (Coca-Cola- and Costco-shaped maps; McDonald's
  live markets, a business line among regions and a region word inside a label stay neutral), no "Its …
  business", "the United States" in running text but never on a bar, card, cover or alt-text label, "annual
  results" and "Figures come from …" — never "as reported" — and the gross-profit definition exactly when a
  gross line is shown (Amazon's FMP-filled gross profit); the three new samples run every matrix above.
* news-v19, names and wording (Batch 1 plan item 5): a single-row Form 4 headline, YouTube title and cover
  name the company once ("… of the company's stock", "… disclosed buying stock"); "Some of the shares";
  "At least one more"; a descriptive investee ("Louisiana data center venture") takes "the" in running
  text, never on a drawn label; the catalogue background stays out of every caption by default
  (STAKE_BACKGROUND_IN_CAPTIONS); and every cover's chip — the period chips dating the earnings, Money Map
  and symbol-less 13F figures — within OPENING_CHIP_MAX_CHARS, the guards mutation-checked.
* news-v19, the review of 2026-10-16 (same version, lexicon re-pinned): Money Map's rest says "Everything
  else" (F6); "by region" also needs the rest at most 10% of revenue (MM-6: Coca-Cola now reads neutral); a
  gross LOSS has its own definition (MM-5); every region name that needs "the" gets it in running text ("The
  U.S. made up …", "the Rest of the World" — MM-3, MM-2's acronyms too); McDonald's part is its International
  Operated Markets (MM-1); a stake text naming a stake-source person is refused (ST-1); a theme's revenue line
  can be a region, read "the United States" (TH-1); a 13F record without its more / fewer counts says the post
  does not show those changes (F13-1); and the Form 4 and Money Map templates refuse a company-name word the
  cover cannot draw, as the others did.
* Batch 3 (2026-10-20): `extra_video_problems` — an EXTRA video post's caption (the platform's own accepted
  caption under the narrated-video disclaimer) passes on every sample, a CEO in the body paragraphs included,
  and is refused for a person in its first line or code-owned suffix, a banned word, a name inside the
  members-elect window, or anything but the derivation; it never raises, and composes nothing new.

Hermetic: pure functions over records built in the test. The Congress block-list is the real
committed roster (`company_news_rules` loads it from `backend/data/`).
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import random
import re
import string
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
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
from app.services.trillion_club.rules import sec_13f_due_date

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


#: Owner decisions 2026-11-20 ("3 - 5 companies at once"): a Form 4 week is never one or two companies. A sample about
#: its LEAD row (`week(late())`, `week(GME)`) is padded with these smaller, timely, unnamed rows of the series' role,
#: filed on the window's last day — so every lead-row behaviour (the hook, the narration, the cards) reads as before.
WEEK_FILLERS = (("WGB", "Woodgrove Bank"), ("WTT", "Wingtip Toys"))


def week_filler(k, series, end, amount):
    sym, name = WEEK_FILLERS[k]
    role = "ceo" if series == "ceo_buys" else ("director", "cfo")[k]
    return R.InsiderPurchase(company=co(sym, name), role=role, person_name=None, amount_usd=amount,
                             shares=round(amount / 50.0), purchases=1, earliest_trade_date=end, latest_trade_date=end,
                             filing_dates=(end,), holding="direct", amended=False)


def week(*rows, series="ceo_buys", start=date(2026, 11, 9), end=date(2026, 11, 15), pad=True):
    """A Form 4 week of `rows` — padded to `R.INSIDER_MIN_ROWS` with WEEK_FILLERS when fewer are given (`pad=False`:
    the rows as given, which the record refuses below three)."""
    rows = list(rows)
    if pad and rows and len(rows) < R.INSIDER_MIN_ROWS:
        low = min(r.amount_usd for r in rows)
        for k in range(R.INSIDER_MIN_ROWS - len(rows)):
            rows.append(week_filler(k, series, end, min(low, (120_000.0, 110_000.0)[k])))
        rows.sort(key=lambda r: (-r.amount_usd, r.company.name.casefold(), r.company.symbol))
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


def late(sym="HSY", name="Hershey", role="director", person=None, *, traded=(date(2026, 10, 14), date(2026, 10, 17)),
         filings=(date(2026, 11, 12),), k=2, holding="direct"):
    """news-v12: a LATE Form 4 row — its trades (the record admits up to INSIDER_MAX_FILING_LAG_DAYS
    before the first filing) are weeks before its filing, so "within two business days of a trade"
    beside "filed Nov 12" would date them to about Nov 10."""
    return R.InsiderPurchase(company=co(sym, name), role=role, person_name=person, amount_usd=150_000.0,
                             shares=812.0, purchases=k, earliest_trade_date=traded[0], latest_trade_date=traded[-1],
                             filing_dates=tuple(filings), holding=holding, amended=False)


def insider_late():
    """news-v12's late row LEADS an insider roundup — owner decisions 2026-11-20: with two smaller rows (a Form 4 post is
    never one or two companies), both filed on time."""
    return week(late(), buy("LTW", "Litware", "director", None, 120_000.0, 3_000.0, 1, [date(2026, 11, 13)]),
                buy("PRSW", "Proseware", "cfo", None, 110_000.0, 4_400.0, 1, [date(2026, 11, 14)]),
                series="insider_buys")


#: Owner decisions 2026-11-20 ("3 - 5 companies at once" — "at most 5, the 5 largest purchases when more qualify"): the
#: five-row roundup (form4_roundup5.jpg's companies), and the seven buyers of a busy week (CEO_ROWS7), which no record
#: holds: the adapter keeps the five largest (final review of 2026-11-26).
INTC = buy("INTC", "Intel", "ceo", None, 1_000_000.0, 40_000.0, 1, [date(2026, 11, 12)])
NEE = buy("NEE", "NextEra Energy", "ceo", None, 265_000.0, 3_600.0, 1, [date(2026, 11, 13)])
KO = buy("KO", "Coca-Cola", "ceo", None, 200_000.0, 3_000.0, 1, [date(2026, 11, 13)])
PEP = buy("PEP", "PepsiCo", "ceo", None, 150_000.0, 1_000.0, 1, [date(2026, 11, 14)])


def ceo_roundup5():
    """Five CEOs (Lowe's held indirectly, Starbucks amended): the picture's five rows have no room for its note — it
    moves to the caption (`news_templates.INS_ROUNDUP_NOTE_MAX_ROWS`)."""
    return week(GME, LOW, INTC, SBUX, NEE)


#: Seven qualifying CEOs, largest total first — a busy week's buyers, more than a record holds (INSIDER_MAX_ROWS = 5).
CEO_ROWS7 = (GME, LOW, INTC, SBUX, NEE, KO, PEP)


def ceo_roundup_of():
    """Lowe's LEADS (the "The chief executive of Lowe's" form — its possessive would read "Lowe's's")."""
    return week(LOW, SBUX, NEE)


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
    """Intersegment eliminations (the shares then read "of combined sales"), an Other bar, no
    gross-profit figure."""
    base = dict(series="money_map", company=co("CMCSA", "Comcast"), fiscal_year="2025", period_end=date(2025, 12, 31),
                segments=(R.Segment("Connectivity and Platforms", 82_000_000_000.0),
                          R.Segment("Content and Experiences", 43_400_000_000.0)),
                other_usd=2_600_000_000.0, eliminations_usd=-4_300_000_000.0, revenue_usd=123_700_000_000.0,
                gross_profit_usd=None, operating_profit_usd=23_300_000_000.0, net_income_usd=16_200_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


# news-v19 (Batch 1, plan item 2): the breakdown's wording follows its PARTS, never a guess.

def coca_cola(**kw):
    """A KO-shaped ALL-REGIONAL breakdown (Coca-Cola reports regional operating segments; here only regions,
    each by its exact `REGION_NAMES` name): the one shape that reads "by region"."""
    base = dict(series="money_map", company=co("KO", "Coca-Cola"), fiscal_year="2025", period_end=date(2025, 12, 31),
                segments=(R.Segment("North America", 19_200_000_000.0),
                          R.Segment("Europe, Middle East and Africa", 8_100_000_000.0),
                          R.Segment("Latin America", 6_900_000_000.0), R.Segment("Asia Pacific", 5_200_000_000.0)),
                other_usd=8_400_000_000.0, eliminations_usd=None, revenue_usd=47_800_000_000.0,
                gross_profit_usd=29_300_000_000.0, operating_profit_usd=13_100_000_000.0,
                net_income_usd=12_100_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


def mcdonalds(**kw):
    """McDonald's fiscal 2025 as the adapter builds it from FMP's product segmentation (read live 2026-10-15):
    FMP labels $13.63B "High-Growth Markets" — a segment of McDonald's 2015-2018 structure — which the adapter
    shows as the International Operated Markets McDonald's has reported since 2019 (the review of 2026-10-16,
    MM-1, checked on EDGAR: `R.SEGMENT_SYMBOL_OVERRIDES`, for McDonald's only; a group of markets it runs, no
    region); "UNITED STATES" $10.82B (shown "United States", `SEGMENT_DISPLAY_OVERRIDES`); and "International
    Developmental Licensed Markets and Corporate" $2.43B (over the 40-character cap: the adapter folds it into
    the rest — "Everything else", true of a line the company breaks out). Mixed, so the NEUTRAL wording."""
    base = dict(series="money_map", company=co("MCD", "McDonald's"), fiscal_year="2025", period_end=date(2025, 12, 31),
                segments=(R.Segment("International Operated Markets", 13_630_000_000.0),
                          R.Segment("United States", 10_820_000_000.0)),
                other_usd=2_430_000_000.0, eliminations_usd=None, revenue_usd=26_880_000_000.0,
                gross_profit_usd=15_100_000_000.0, operating_profit_usd=12_300_000_000.0,
                net_income_usd=8_500_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


def amazon(**kw):
    """An Amazon-shaped fiscal 2025 (the 2026-10-09 live preview: Online Stores $269.3B, Third-Party Seller
    Services $172.2B, "Amazon Web Services" $128.7B …) with its cloud line under the short name Amazon itself
    uses, "AWS" (`SEGMENT_ACRONYMS_OK` keeps it wherever a feed writes it so), and a gross profit FMP FILLS as
    revenue minus cost of revenue — Amazon reports no gross subtotal — which is why the post explains gross
    profit and never says "as reported"."""
    base = dict(series="money_map", company=co("AMZN", "Amazon"), fiscal_year="2025", period_end=date(2025, 12, 31),
                segments=(R.Segment("Online Stores", 270_000_000_000.0),
                          R.Segment("Third-Party Seller Services", 170_000_000_000.0),
                          R.Segment("AWS", 128_000_000_000.0), R.Segment("Advertising Services", 69_000_000_000.0),
                          R.Segment("Subscription Services", 49_000_000_000.0)),
                other_usd=31_000_000_000.0, eliminations_usd=None, revenue_usd=717_000_000_000.0,
                gross_profit_usd=360_000_000_000.0, operating_profit_usd=80_000_000_000.0,
                net_income_usd=77_000_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


# ── drop 2b records ───────────────────────────────────────────────────────────

CONGRESS_RUN = date(2026, 12, 8)     # Tue, day 8..14: the first Congress Count day (contract §0, 2b-1)
STAKES_RUN = date(2026, 12, 29)      # Tue: the first company_stakes day (2b-2)
EARNINGS_RUN = date(2026, 11, 12)    # Thu, inside the Q3 earnings season
THEME_RUN = date(2026, 11, 16)       # Mon (a theme is a fallback on any posting day)


def congress_row(**kw):
    """The scrubbed Congress feed (`~/.claude/plans/company-weekly-congress-probe/`, names removed at
    the source): among September 2026's disclosures, Accenture had stock purchases by 3 distinct
    members — that realistic count, read for November on the first Congress Count Tuesday."""
    base = dict(series="congress_count", company=co("ACN", "Accenture"), month="2026-11", members=3,
                fetched_on=CONGRESS_RUN)
    base.update(kw)
    return R.CongressCount(**base)


#: news-v23 (the restyle, owner 2026-11-19: "2-3 companies at once"): the sample month's other companies — fictional,
#: each at the 2-member floor, drawn with their wordmarks.
CONGRESS_OTHERS = (("CTSO", "Contoso"), ("NWT", "Northwind"))


def congress_month(rows, *, month=None, fetched_on=None):
    """A `CongressMonth` of `rows` (its month and read are the first row's) — WITHOUT the record's own validation when a
    row slipped past a gate (`_unchecked` rows: a forged record for the template's backstop)."""
    month = rows[0].month if month is None else month
    fetched_on = rows[0].fetched_on if fetched_on is None else fetched_on
    try:
        return R.CongressMonth(series="congress_count", month=month, fetched_on=fetched_on, counts=tuple(rows))
    except ValueError:
        return _unchecked_record(R.CongressMonth, series="congress_count", month=month, fetched_on=fetched_on,
                                 counts=tuple(rows))


def congress(*, others=2, lead=None, **kw):
    """ONE Congress Count post (news-v23): the lead row (`congress_row(**kw)`, Accenture's 3 members, or `lead`) then
    `others` of CONGRESS_OTHERS at 2 members, the same month and read."""
    row = congress_row(**kw) if lead is None else lead
    rest = [congress_row(company=co(s, n), members=2, month=row.month, fetched_on=row.fetched_on)
            for s, n in CONGRESS_OTHERS[:others]]
    return congress_month([row, *rest])


def stake(**kw):
    """A stake ROW of a company_stakes post (news-v23, owner 2026-11-19 "public only": every row a PUBLIC company — a
    listed U.S. symbol the adapter checked is actively trading): NVIDIA → Intel by default, the catalogue's own row
    (`data/trillion_club_seed.json`). `compose` / `_parts` below wrap a bare row in its one-stake post (`one`)."""
    base = dict(series="company_stakes", stake_id="4e6a8c0e-2b4d-4c6e-8a0c-2e4a6c8e0a06", investor=co("NVDA", "NVIDIA"),
                investee_name="Intel", investee=co("INTC", "Intel"), kind="on_13f_note", value_usd=5_000_000_000.0,
                value_basis="invested", ownership_pct=None, as_of=date(2025, 12, 26), verified_on=date(2026, 9, 24),
                source_title="Intel 8-K (Dec 29, 2025)",
                background="Agreed on Sep 15, 2025, alongside a product collaboration with NVIDIA.",
                listed_since=None, local_listing=None, is_new=False)
    base.update(kw)
    return R.CompanyStake(**base)


def stakes(*rows, investor=None):
    """ONE company_stakes post of `rows` (`R.CompanyStakes`), ranked by `R.stake_rank_key` — WITHOUT the record's own
    validation when a row cannot stand in one (a forged record, for the template's backstop)."""
    rows = tuple(sorted(rows, key=R.stake_rank_key))
    inv = investor if investor is not None else rows[0].investor
    try:
        return R.CompanyStakes(series="company_stakes", investor=inv, stakes=rows)
    except ValueError:
        return _unchecked_record(R.CompanyStakes, series="company_stakes", investor=inv, stakes=rows)


def one(row):
    """The one-stake post of `row`."""
    return stakes(row)


def intel_stake():
    """On the investor's 13F, with a verified U.S. listing (the investee's own CompanyRef and logo)."""
    return stake()


def tesla_spacex():
    """A U.S.-listed investee with its own logo, at fair value; the background carries a "%" (dropped)."""
    return stake(stake_id="7a9e2c44-1b3d-4f5a-8c6e-9d0f1a2b3c03", investor=co("TSLA", "Tesla"), investee_name="SpaceX",
                 investee=co("SPCX", "SpaceX"), kind="us_listed_off_13f", value_usd=3_007_000_000.0,
                 value_basis="fair_value", as_of=date(2026, 6, 30), source_title="Tesla 10-Q (Q2 2026)",
                 background="Tesla invested $2.00B in March 2026 for under 1% (formerly xAI preferred).")


def synopsys_stake():
    return stake(stake_id="5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01", investee_name="Synopsys",
                 investee=co("SNPS", "Synopsys"), value_usd=2_000_000_000.0, as_of=date(2025, 12, 1),
                 source_title="Synopsys 8-K (Dec 1, 2025)", background=None)


def coherent_stake():
    return stake(stake_id="0d3b6f1e-2a4c-4e8b-9f10-6c2d8a7e5b02", investee_name="Coherent",
                 investee=co("COHR", "Coherent"), value_usd=2_000_000_000.0, as_of=date(2026, 3, 2),
                 source_title="Coherent 8-K (Mar 2, 2026)", background=None)


def corning_stake():
    """A 10-Q's quarter-end figure: its "(quarter ended …)" is left off the picture (the row's as-of date says it)."""
    return stake(stake_id="2c4e6a8b-0d1f-4a3c-9e5b-7d9f1b3d5f04", investee_name="Corning",
                 investee=co("GLW", "Corning"), kind="us_listed_off_13f", value_usd=500_000_000.0,
                 as_of=date(2026, 6, 30), source_title="Corning 10-Q (quarter ended Jun 30, 2026)", background=None)


def lumentum_stake():
    return stake(stake_id="6d8f0a2c-4e6a-4c8e-9a1c-3e5a7c9e1b08", investee_name="Lumentum",
                 investee=co("LITE", "Lumentum"), kind="us_listed_off_13f", value_usd=2_000_000_000.0,
                 as_of=date(2026, 6, 27), source_title="Lumentum 10-K (fiscal year ended Jun 27, 2026)",
                 background=None)


def nvidia_stakes():
    """NVIDIA's three catalogue stakes in public companies with a disclosed figure (stakes_nvidia3.jpg)."""
    return stakes(intel_stake(), synopsys_stake(), coherent_stake())


def nvidia_stakes_more():
    """Four — a post's most (RESTYLE_SPEC §2.5, "1-4 PUBLIC investees"; stakes_nvidia.jpg's rows): the picture draws all
    four, the cover three and "+1 more stake in the caption" (review 2026-11-20)."""
    return stakes(intel_stake(), synopsys_stake(), coherent_stake(), corning_stake())


def public_commitment():
    """A commitment to a PUBLIC company (fictional Contoso): "committed up to", the commitment line."""
    return stake(stake_id="9b1d3f5a-7c9e-4b2d-8f0a-1c3e5a7b9d05", investee_name="Contoso",
                 investee=co("CTSO", "Contoso"), kind="commitment", value_usd=1_000_000_000.0,
                 value_basis="committed_up_to", as_of=date(2026, 6, 30),
                 source_title="Contoso 10-Q (quarter ended Jun 30, 2026)", background=None)


def berkshire_mixed():
    """Two bases in one post (Berkshire Hathaway, fictional public investees): a carrying value and a fair value —
    the "mixed" figure line and card."""
    brk = co("BRK-B", "Berkshire Hathaway")
    return stakes(
        stake(stake_id="1f3a5c7e-9b0d-4f2a-8c4e-6a8c0e2a4c09", investor=brk, investee_name="Fabrikam",
              investee=co("FBRK", "Fabrikam"), kind="us_listed_off_13f", value_usd=461_000_000.0,
              value_basis="carrying_value", ownership_pct=50.0, as_of=date(2026, 6, 30),
              source_title="Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026)",
              background="Jefferies Financial Group owned the other 50%."),
        stake(stake_id="3b5d7f91-a2c4-4e6f-8b0d-2f4a6c8e0b10", investor=brk, investee_name="Litware",
              investee=co("LTWR", "Litware"), kind="on_13f_note", value_usd=8_886_000_000.0, value_basis="fair_value",
              as_of=date(2025, 12, 31), source_title="Berkshire Hathaway 2025 shareholder letter",
              background="Cost basis was $4.17B, per the letter."))


def earnings_row(**kw):
    """The templates design's S3 (fictional Northwind Fitness): a smaller loss than estimated."""
    base = dict(series="earnings", company=co("NWFT", "Northwind Fitness"), report_date=date(2026, 11, 5),
                period_end=date(2026, 9, 30), eps_actual=-0.05, eps_estimate=-0.12, revenue_actual=551_900_000.0,
                revenue_estimate=543_600_000.0)
    base.update(kw)
    return R.EarningsReport(**base)


#: news-v23 (the restyle, owner 2026-11-19: "combine 2 to 4 companies at once"): the sample day's other companies —
#: fictional, each with a clear EPS surprise and a plausible revenue pair (Contoso's revenue under its estimate).
EARNINGS_OTHERS = (
    dict(company=co("CTSO", "Contoso"), eps_actual=1.12, eps_estimate=1.05, revenue_actual=3_200_000_000.0,
         revenue_estimate=3_300_000_000.0),
    dict(company=co("LTW", "Litware"), eps_actual=0.62, eps_estimate=0.70, revenue_actual=880_000_000.0,
         revenue_estimate=860_000_000.0),
    dict(company=co("ADVW", "Adventure Works"), eps_actual=2.40, eps_estimate=2.10, revenue_actual=12_400_000_000.0,
         revenue_estimate=12_100_000_000.0),
)


def earnings_day(rows):
    """An `EarningsDay` of `rows` — WITHOUT the record's own validation when needed (a forged record)."""
    try:
        return R.EarningsDay(series="earnings", reports=tuple(rows))
    except ValueError:
        return _unchecked_record(R.EarningsDay, series="earnings", reports=tuple(rows))


def earnings(*, others=1, lead=None, **kw):
    """ONE earnings post (news-v23): the lead report (`earnings_row(**kw)`, or `lead`) then `others` of
    EARNINGS_OTHERS, reported the same day, for the same quarter."""
    row = earnings_row(**kw) if lead is None else lead
    # the others' own quarter — the last quarter end before the report (or none, with the lead)
    d = row.report_date
    period = None if row.period_end is None else max(
        q for q in (date(d.year - 1, 12, 31), date(d.year, 3, 31), date(d.year, 6, 30), date(d.year, 9, 30))
        if q < d)
    rest = [R.EarningsReport(series="earnings", report_date=row.report_date, period_end=period, **o)
            for o in EARNINGS_OTHERS[:others]]
    return earnings_day([row, *rest])


def _unchecked_record(cls, **fields_):
    """A record of `cls` built WITHOUT its own validation (test-only: a forged record for the template's backstop)."""
    obj = object.__new__(cls)
    for f in cls.__slots__:
        object.__setattr__(obj, f, fields_[f])
    return obj


def earnings_profit():
    """Fictional Fabrikam: earnings, no period end (the adapter's scale-check contingency), and a revenue pair
    the post cannot WRITE — $2.3456B vs $2.3455B: a $100K gap no shared precision up to three places shows
    (`revenue_pair` is None), so revenue is left out of every string. news-v19: a record WITHOUT a revenue pair
    is refused (`test_earnings_refusals[no_revenue]`); this one has a checked pair, and its composition is
    byte-identical to the news-v18 golden (no revenue line anywhere)."""
    return earnings(company=co("FBKM", "Fabrikam"), report_date=date(2026, 11, 10), period_end=None, eps_actual=4.86,
                    eps_estimate=4.5, revenue_actual=2_345_600_000.0, revenue_estimate=2_345_500_000.0)


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


#: news-v23 phase 3 (the weekly theme post): a WEDNESDAY run, the key naming the theme, its angle and the run date.
THEME_WED = date(2026, 11, 25)


def ai_chips_weekly():
    """Angle 1 as the weekly post builds it: the same AI chips members, a Wednesday run, the new key."""
    return theme(theme_size=16, angle="make_money", posted_on=THEME_WED, tickers_as_of=date(2026, 11, 18))


#: (symbol, name, market value) — REAL names, SAMPLE values (theme_biggest_power.jpg's own sample figures).
POWER_MACHINE = [("GEV", "GE Vernova", 171e9), ("NEE", "NextEra Energy", 152e9), ("ETN", "Eaton", 134e9),
                 ("CEG", "Constellation Energy", 98e9), ("VRT", "Vertiv", 52e9), ("PWR", "Quanta Services", 50e9),
                 ("D", "Dominion Energy", 48e9), ("VST", "Vistra", 45e9), ("CCJ", "Cameco", 34e9),
                 ("BWXT", "BWX Technologies", 12e9), ("OKLO", "Oklo", 9.8e9), ("SMR", "NuScale Power", 4.4e9),
                 ("LEU", "Centrus Energy", 2.5e9), ("NNE", "Nano Nuclear Energy", 1.2e9)]


def power_machine(rows=POWER_MACHINE, **kw):
    """Angle 2, "who's biggest": the members ranked by a dated market value; 14 of the theme's 16 companies."""
    base = dict(series="theme_explainer", slug="powering-machine", title="Power the Machine",
                members=tuple(R.ThemeMember(company=co(s, n), top_segment=None, top_segment_share=None,
                                            fiscal_year=None, market_value_usd=v) for s, n, v in rows),
                tickers_as_of=date(2026, 11, 18), theme_size=16, angle="biggest", posted_on=THEME_WED,
                values_as_of=date(2026, 11, 24))
    base.update(kw)
    return R.ThemeExplainer(**base)


#: news-v23 phase 3, the theme's last three angles (2026-11-20) — REAL names, SAMPLE figures (each mock's own sample
#: shape): angle 3 "growth + profit" (theme_growth_profit_silicon.jpg), angle 4 the insider scorecard
#: (theme_insiders_cyber.jpg), angle 5 Congress (theme_congress_defense.jpg).
SILICON_GROWTH = [("NVDA", "NVIDIA", 214, 100, 120, "2026"), ("TSM", "TSMC", 134, 100, 53.6, "2025"),
                  ("AVGO", "Broadcom", 144, 100, 31.7, "2025"), ("ASML", "ASML", 102, 100, 27.5, "2025"),
                  ("AMD", "AMD", 114, 100, 6.8, "2025"), ("MU", "Micron Technology", 149, 100, 16.4, "2025"),
                  ("ARM", "Arm Holdings", 124, 100, 23.6, "2026"), ("MRVL", "Marvell Technology", 105, 100, -17.9, "2026"),
                  ("QCOM", "Qualcomm", 109, 100, 28.3, "2025"), ("AMAT", "Applied Materials", 102, 100, 26.5, "2025"),
                  ("INTC", "Intel", 98, 100, -19.0, "2025"), ("TXN", "Texas Instruments", 106, 100, 30.0, "2025")]


def silicon_growth(rows=SILICON_GROWTH, **kw):
    base = dict(series="theme_explainer", slug="silicon-rush", title="Silicon Rush",
                members=tuple(R.ThemeMember(company=co(s, n), top_segment=None, top_segment_share=None, fiscal_year=fy,
                                            revenue_usd=r * 1e8, prior_revenue_usd=p * 1e8, net_income_usd=ni * 1e8)
                              for s, n, r, p, ni, fy in rows),
                tickers_as_of=date(2026, 11, 18), theme_size=21, angle="growth_profit", posted_on=THEME_WED)
    base.update(kw)
    return R.ThemeExplainer(**base)


CYBER_INSIDERS = [("RPD", "Rapid7", 2.1e6, 0.0), ("S", "SentinelOne", 1.2e6, 8.6e6), ("TENB", "Tenable", 640e3, 0.0),
                  ("CRWD", "CrowdStrike", 0.0, 186e6), ("NET", "Cloudflare", 0.0, 128e6),
                  ("PANW", "Palo Alto Networks", 0.0, 94e6), ("FTNT", "Fortinet", 0.0, 63e6),
                  ("ZS", "Zscaler", 0.0, 41e6), ("OKTA", "Okta", 0.0, 22e6), ("QLYS", "Qualys", 0.0, 14e6),
                  ("VRNS", "Varonis", 0.0, 9.8e6)]


def cyber_insiders(rows=CYBER_INSIDERS, **kw):
    base = dict(series="theme_explainer", slug="cyber-wars", title="Cyber Wars",
                members=tuple(R.ThemeMember(company=co(s, n), top_segment=None, top_segment_share=None,
                                            fiscal_year=None, purchases_usd=b, sales_usd=sl) for s, n, b, sl in rows),
                tickers_as_of=date(2026, 11, 18), theme_size=15, angle="insiders", posted_on=THEME_WED,
                window_start=THEME_WED - timedelta(days=90), window_end=THEME_WED - timedelta(days=1),
                companies_checked=15)
    base.update(kw)
    return R.ThemeExplainer(**base)


BATTLEFIELD_CONGRESS = [("PLTR", "Palantir", 6), ("LMT", "Lockheed Martin", 4), ("RTX", "RTX", 3),
                        ("GD", "General Dynamics", 2), ("KTOS", "Kratos", 2)]


def battlefield_congress(rows=BATTLEFIELD_CONGRESS, **kw):
    base = dict(series="theme_explainer", slug="modern-battlefield", title="Modern Battlefield",
                members=tuple(R.ThemeMember(company=co(s, n), top_segment=None, top_segment_share=None,
                                            fiscal_year=None, congress_members=c) for s, n, c in rows),
                tickers_as_of=date(2026, 11, 18), theme_size=16, angle="congress", posted_on=THEME_WED, month="2026-10")
    base.update(kw)
    return R.ThemeExplainer(**base)


# ── news-v22 (2026-11-15): the spotlight lane's records (fictional companies) ─────

SPOT_THU, SPOT_SAT, SPOT_TUE = date(2026, 11, 19), date(2026, 11, 21), date(2026, 11, 17)
#: A run outside the members-elect window (Election Day 2026 through Jan 3, 2027, judged over the run day and the
#: two after it): its Form 4 people may be named — in body text only.
SPOT_JAN = date(2027, 1, 5)


def ip(sym, name, role, person, amount, shares, k, filings, trades, cost, holding="direct"):
    """One spotlight person: `cost` is (lowest, highest) of the Form 4 lines' own cost per share, as filed — the
    record checks the row's average (amount / shares) lies between them."""
    return R.InsiderPurchase(company=co(sym, name), role=role, person_name=person, amount_usd=amount, shares=shares,
                             purchases=k, earliest_trade_date=trades[0], latest_trade_date=trades[-1],
                             filing_dates=tuple(filings), holding=holding, amended=False,
                             cost_per_share_min_usd=cost[0], cost_per_share_max_usd=cost[1])


def isp_company(lead, *others):
    """One row of the insider LIST (owner decisions 2026-11-20): an issuer's lead and its other insiders."""
    return R.InsiderSpotlightCompany(lead=lead, others=tuple(others))


def isp_filler(sym, name, role, amount, cost, day):
    """A small one-person row filed on `day` (its trade the same day: never late), `amount` at the one `cost` a share
    — a list's second and third company, so a sample about ONE issuer is still a 3-company post."""
    shares = round(amount / cost)
    return isp_company(ip(sym, name, role, None, shares * cost, float(shares), 1, [day], [day], (cost, cost)))


def isp_list(*companies, start, end):
    """The insider LIST record of `companies` (rows or bare lead purchases), in the record's order
    (`R.spotlight_company_order`: the largest total first)."""
    rows = [c if isinstance(c, R.InsiderSpotlightCompany) else isp_company(c) for c in companies]
    return R.InsiderSpotlight(series="insider_spotlight", window_start=start, window_end=end,
                              companies=tuple(sorted(rows, key=R.spotlight_company_order)))


def spotlight(lead, *others, start, end, segment=None):
    """The insider LIST whose LEAD row is `lead` and its `others` (one issuer) — owner decisions 2026-11-20: never one
    company, so two small rows filed on `end` (Litware's director, Proseware's CFO) make it a 3-company post. The
    one-issuer post's revenue line (`segment`) is retired: ignored."""
    return isp_list(isp_company(lead, *others), isp_filler("LTW", "Litware", "director", 140_000.0, 40.0, end),
                    isp_filler("PRSW", "Proseware", "cfo", 120_000.0, 25.0, end), start=start, end=end)


def isp_cluster():
    """The design's worked example 1 (design_content step 10): Contoso's CEO, 7,000 shares at $142.10 and 7,000 at
    $143.85 on ONE Form 4 day (Nov 18, trades Nov 16-17), and two more insiders of the same issuer — a director
    (Nov 17) and the CFO (Nov 18) — with Contoso's largest revenue line, read on Thu Nov 19, 2026 (the window
    Nov 17-18). Role-only (no record name), as the members-elect window would make it anyway."""
    return spotlight(
        ip("CTSO", "Contoso", "ceo", None, 2_001_650.0, 14_000.0, 2, [date(2026, 11, 18)],
           [date(2026, 11, 16), date(2026, 11, 17)], (142.10, 143.85)),
        ip("CTSO", "Contoso", "director", None, 410_002.0, 2_900.0, 1, [date(2026, 11, 17)], [date(2026, 11, 16)],
           (141.38, 141.38)),
        ip("CTSO", "Contoso", "cfo", None, 250_008.0, 1_760.0, 1, [date(2026, 11, 18)], [date(2026, 11, 17)],
           (142.05, 142.05)),
        start=date(2026, 11, 17), end=date(2026, 11, 18), segment=("Cloud Services", 0.54, "2025"))


def isp_single():
    """One insider, LATE: Fabrikam's CFO filed on Mon Nov 16 two purchases traded Nov 5-6 — more than two federal
    business days before (Veterans Day is not one) — so the trade dates are stated and the Form 4 explainer drops
    the two-business-day rule; a cost RANGE; no revenue line (Fabrikam is not in the audited pool)."""
    return spotlight(
        ip("FBKM", "Fabrikam", "cfo", None, 612_500.0, 25_000.0, 2, [date(2026, 11, 16)],
           [date(2026, 11, 5), date(2026, 11, 6)], (24.10, 24.90)),
        start=date(2026, 11, 14), end=date(2026, 11, 16))


def isp_indirect():
    """A director whose shares are held indirectly (the picture's fourth fact is the holding, the cluster stays in
    the headline), one more director of the same issuer ("another director"), and a REGIONAL revenue line that
    takes "the" in running text; read on Sat Nov 21 (the window Thu-Fri)."""
    return spotlight(
        ip("NWND", "Northwind", "director", None, 1_250_000.0, 50_000.0, 1, [date(2026, 11, 19)],
           [date(2026, 11, 18)], (25.0, 25.0), holding="indirect"),
        ip("NWND", "Northwind", "director", None, 300_000.0, 12_000.0, 1, [date(2026, 11, 20)],
           [date(2026, 11, 19)], (25.0, 25.0)),
        start=date(2026, 11, 19), end=date(2026, 11, 20), segment=("United States", 0.61, "2025"))


def isp_named():
    """Outside the members-elect window (Tue Jan 5, 2027): the CEO and the CFO are NAMED — only in the caption
    paragraphs after the headline, never in the headline, the picture, the video or the alt text. Two filing
    days for the CEO (Form 4s, Dec 31 and Jan 4 — New Year's Day is no business day), the window Dec 30-Jan 4 (the
    previous spotlight ran Thu Dec 31), a window across the year end."""
    return spotlight(
        ip("TLSP", "Tailspin Toys", "ceo", "Jordan Avery", 3_480_000.0, 120_000.0, 3,
           [date(2026, 12, 31), date(2027, 1, 4)], [date(2026, 12, 30), date(2026, 12, 31)], (28.75, 29.25)),
        ip("TLSP", "Tailspin Toys", "cfo", "Casey Morgan", 145_000.0, 5_000.0, 1, [date(2027, 1, 4)],
           [date(2026, 12, 31)], (29.0, 29.0)),
        start=date(2026, 12, 30), end=date(2027, 1, 4))


def f13_list(moves, *, filer="Fabrikam Capital", amended_on=None, filed=date(2026, 11, 13), eligible=None,
             changes=None, filer_symbol=None):
    """A 13F spotlight of `moves` (put in the record's order, `R.thirteen_f_spotlight_order`): ``eligible`` the filing's
    eligible holdings (default: the ones given), ``changes`` every change its counts report (None: a count withheld)."""
    ordered = tuple(sorted(moves, key=R.thirteen_f_spotlight_order))
    return R.ThirteenFSpotlight(series="thirteen_f_spotlight", filer_name=filer, filer_cik="0009999902",
                                filer_symbol=filer_symbol, period="2026-Q3", period_end=date(2026, 9, 30),
                                filed_on=filed, amended_on=amended_on, moves=ordered,
                                eligible_moves=len(ordered) if eligible is None else eligible, filing_moves=changes)


#: The 13F spotlight LIST's holdings (owner feedback 2026-11-19: "show a list of what Fabrikam Capital did, increase or
#: reduce shares of top 5 companies. not just one"): each kind, exact counts unless named "rounded".
F13L_FEWER = dict(sym="NWT", name="Northwind", kind="decreased", shares=41_900_000.0, prev_shares=60_000_000.0,
                  value_usd=3_100_000_000.0)
F13L_MORE = dict(sym="LTW", name="Litware", kind="increased", shares=12_500_000.0, prev_shares=8_000_000.0,
                 value_usd=900_000_000.0)
F13L_NEW = dict(sym="ADVW", name="Adventure Works", kind="newly_reported", shares=5_600_000.0, prev_shares=0.0,
                value_usd=1_240_000_000.0, listed_on=date(2026, 8, 13))
F13L_GONE = dict(sym="WGB", name="Woodgrove Bank", kind="no_longer_reported", shares=0.0, prev_shares=4_300_000.0,
                 prev_value_usd=310_000_000.0)
F13L_FEWER2 = dict(sym="CTSO", name="Contoso", kind="decreased", shares=2_000_000.0, prev_shares=3_000_000.0,
                   value_usd=150_000_000.0)


def _m(spec, **over):
    kw = dict(spec, **over)
    return move(kw.pop("sym"), kw.pop("name"), kw.pop("kind"), **kw)


def f13s_list2():
    """Two holdings, reduced and increased — and they are EVERY change the filing's counts report (2), so the counts
    are stated as the filing's: "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased."."""
    return f13_list([_m(F13L_FEWER), _m(F13L_MORE)], changes=2)


def f13s_list5():
    """Five holdings of all four kinds, the largest dollar size first (`R.thirteen_f_move_flow`: Northwind's ~$1.34B
    fewer shares, Adventure Works' $1.24B new holding, Litware's $324M more, Woodgrove Bank's $310M gone, Contoso's $75M
    fewer), out of 12 changes the filing reports — so "5 of the holding changes", never a count that reads as the
    filing's."""
    return f13_list([_m(F13L_FEWER), _m(F13L_NEW), _m(F13L_MORE), _m(F13L_GONE), _m(F13L_FEWER2)], changes=12)


def f13s_list_truncated():
    """Five eligible holdings out of 31 changes the filing reports: "5 of its holding changes" — never "its 5 largest
    changes" (the rows are ranked by dollar size and drawn in shares). news-v23: a record holds up to eight
    (`R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES`), so a cut shows as "+N more changes in the caption" (`f13s_list8`)."""
    extra = [_m(F13L_FEWER2, sym=s, name=n, value_usd=v)
             for s, n, v in (("TLSP", "Tailspin Toys", 120_000_000.0), ("WWI", "Wide World Importers", 60_000_000.0))]
    return f13_list([_m(F13L_FEWER), _m(F13L_NEW), _m(F13L_MORE), *extra], changes=31)


def f13s_list_amended():
    """An amended filing (13F-HR/A) of three holdings that are every change it reports (3): a new one and two no longer
    listed — the footer, the cover's card 1 and the period paragraph say "amended"; read on Thu Nov 19."""
    return f13_list([_m(F13L_NEW), _m(F13L_GONE),
                     _m(F13L_GONE, sym="FRCO", name="Fourth Coffee", prev_shares=1_250_000.0, prev_value_usd=95e6)],
                    amended_on=date(2026, 11, 18), filed=date(2026, 11, 12), changes=3)


def f13s_list_rounded():
    """Rounded counts (filed to the share), a filer whose name ends in "s" ("Proseware Advisors' latest 13F"), three
    holdings out of 9 changes: the picture's figures drop "about" and its column heading says "rounded"; the captions
    keep "about"."""
    return f13_list([_m(F13L_FEWER, shares=41_912_345.0, prev_shares=60_123_456.0),
                     _m(F13L_MORE, shares=12_534_567.0, prev_shares=8_012_345.0),
                     _m(F13L_GONE, prev_shares=4_251_234.0)], filer="Proseware Advisors", changes=9)


def f13s_list8():
    """news-v23 (owner 2026-11-19, "+N more changes in the caption"): EIGHT eligible holdings of every kind in the
    record (`R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES`), the picture drawing the largest six and "+2 more changes in the
    caption", the cover four and "+4 more …" — the captions that ride either list the rest (X and Bluesky cannot:
    dropped, `over_budget`)."""
    extra = [_m(F13L_FEWER2, sym=s, name=n, value_usd=v)
             for s, n, v in (("TLSP", "Tailspin Toys", 120_000_000.0), ("WWI", "Wide World Importers", 60_000_000.0))]
    gone2 = _m(F13L_GONE, sym="FRCO", name="Fourth Coffee", prev_shares=1_250_000.0, prev_value_usd=95e6)
    return f13_list([_m(F13L_FEWER), _m(F13L_NEW), _m(F13L_MORE), _m(F13L_GONE), _m(F13L_FEWER2), gone2, *extra],
                    changes=31)


def f13s_list_listed():
    """news-v23: a filer with its OWN listing (a Trillion Club company's 13F): its plate — its logo and ticker chip —
    sits beside the title on the picture and the cover."""
    return f13_list([_m(F13L_FEWER), _m(F13L_NEW), _m(F13L_GONE)], filer="Fabrikam Holdings", filer_symbol="FBKM",
                    changes=3)


#: The layouts as shipped in THIS checkout (pair and grid stay refused until their series ship).
REAL_SHIPPED_LAYOUTS = onscreen.SHIPPED_LAYOUTS


@pytest.fixture(autouse=True)
def _pair_and_grid_validate(monkeypatch):
    """The 2b `pair` (stakes) and `grid` (theme) images validate only once SHIPPED_LAYOUTS lists them
    (the plumbing step flips both sides with SHIPPED_SERIES). Every test here composes with them
    allowed IN MEMORY — the state that flip produces; the 2a layouts are untouched by it.
    `test_pair_and_grid_series_are_refused_until_their_layouts_ship` restores the real tuple."""
    monkeypatch.setattr(onscreen, "SHIPPED_LAYOUTS", onscreen.LAYOUTS)


#: The REAL members-elect window (owner decision 4), kept before the autouse fixture below replaces it.
REAL_MEMBERS_ELECT_WINDOW = R.members_elect_window


@pytest.fixture(autouse=True)
def _names_outside_the_members_elect_window(monkeypatch):
    """news-v19 (owner decision 4): the Form 4 samples run on Mon Nov 16, 2026 — INSIDE the members-elect
    window (Election Day Nov 3, 2026 through Jan 3, 2027), where every row is role-only. The goldens and the
    placement matrices pin the NAMED composition (what any day outside the window composes), so the window
    is switched off here; the members-elect tests below restore the real one (`REAL_MEMBERS_ELECT_WINDOW`)
    and pin the role-only composition, the boundaries and the validator against it."""
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)


#: (name, record factory, run date) — every sample the matrices walk.
SAMPLES = [
    ("ceo_roundup", ceo_roundup, MON),
    # owner decisions 2026-11-20 ("3 - 5 companies at once"): the one-company Form 4 post is gone
    ("ceo_roundup5", ceo_roundup5, MON),
    ("ceo_roundup_of", ceo_roundup_of, MON),
    ("insider_mixed", insider_mixed, MON),
    ("insider_late", insider_late, MON),
    ("berkshire_13f", berkshire, TUE),
    ("pershing_13f", pershing, TUE),
    ("costco_money_map", costco, THU),
    ("rivian_money_map", rivian, THU),
    ("comcast_money_map", comcast, THU),
    # news-v19 (Batch 1): all regions → "by region"; mixed markets → neutral; Amazon's AWS and gross line
    ("coca_cola_money_map", coca_cola, THU),
    ("mcdonalds_money_map", mcdonalds, THU),
    ("amazon_money_map", amazon, THU),
    # drop 2b
    ("congress_accenture", congress, CONGRESS_RUN),
    # news-v23 (owner 2026-11-19, "public only" + "add 1-3 more companies"): one investor's stakes in public companies
    ("stakes_nvidia", nvidia_stakes, STAKES_RUN),
    ("stakes_nvidia_more", nvidia_stakes_more, STAKES_RUN),
    ("stakes_berkshire_mixed", berkshire_mixed, STAKES_RUN),
    ("stake_spacex_fair_value", lambda: one(tesla_spacex()), STAKES_RUN),
    ("stake_intel_us", lambda: one(intel_stake()), STAKES_RUN),
    ("stake_commitment_public", lambda: one(public_commitment()), STAKES_RUN),
    ("earnings_loss", earnings, EARNINGS_RUN),
    ("earnings_profit", earnings_profit, EARNINGS_RUN),
    ("theme_ai_chips", ai_chips, THEME_RUN),
    ("theme_ai_chips_of_more", ai_chips_of_more, THEME_RUN),
    # news-v23 phase 3 (the weekly theme post, Wednesdays): angle 1 under its new key, angle 2 by market value
    ("theme_ai_chips_weekly", ai_chips_weekly, THEME_WED),
    ("theme_power_machine", power_machine, THEME_WED),
    # news-v23 phase 3 (2026-11-20): the last three angles — growth + profit, the insider scorecard, Congress
    ("theme_growth_silicon", silicon_growth, THEME_WED),
    ("theme_insiders_cyber", cyber_insiders, THEME_WED),
    ("theme_congress_battlefield", battlefield_congress, THEME_WED),
    # news-v22 (2026-11-15): the spotlight lane
    ("isp_cluster", isp_cluster, SPOT_THU),
    ("isp_single", isp_single, SPOT_TUE),
    ("isp_indirect", isp_indirect, SPOT_SAT),
    ("isp_named_outside_window", isp_named, SPOT_JAN),
    # owner feedback 2026-11-19 / news-v23 (the restyle): the 13F LIST — the one 13F design (two to six rows on the
    # picture, the largest dollar size first; no single-holding post)
    ("f13s_list2", f13s_list2, SPOT_SAT),
    ("f13s_list5", f13s_list5, SPOT_SAT),
    ("f13s_list_truncated", f13s_list_truncated, SPOT_TUE),
    ("f13s_list_amended", f13s_list_amended, SPOT_THU),
    ("f13s_list_rounded", f13s_list_rounded, SPOT_SAT),
    ("f13s_list_listed", f13s_list_listed, SPOT_SAT),
]


def compose(rec, run_date, store_state="live", allow_x_url=False):
    if isinstance(rec, R.CompanyStake):          # news-v23: a bare stake row is its one-stake post
        rec = one(rec)
    return T.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url)


def _parts(rec, run_date):
    """The builder's parts (every variant offered), before the assembler picks."""
    if isinstance(rec, R.CompanyStake):
        rec = one(rec)
    builder = T._BUILDERS[rec.series]
    return builder(rec, run_date, frozenset()) if builder in T._PERSON_BUILDERS else builder(rec, run_date)


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
                      'headline': "At least 3 CEOs disclosed buying their own company's stock",
                      'list': {'rows': [{'logo': 'GME', 'chip': 'GME', 'cells': ['GameStop', 'CEO', '$74.4M']},
                                        {'logo': 'LOW', 'chip': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                        {'logo': 'SBUX', 'chip': 'SBUX', 'cells': ['Starbucks', 'CEO', '$410K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 3 CEOs disclosed buying their own company's stock",
                    # news-v23: each row's ticker chip (mock_form4.wide_row_card)
                    'sections': [{'rows': [{'logo': 'GME', 'chip': 'GME', 'cells': ['GameStop', 'CEO', '$74.4M']},
                                           {'logo': 'LOW', 'chip': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                           {'logo': 'SBUX', 'chip': 'SBUX',
                                            'cells': ['Starbucks', 'CEO', '$410K']}]}],
                    # news-v12: the SHARES are held indirectly, not the purchases
                    'notes': ['Some of the shares in these purchases are reported as held indirectly.',
                              'The figures include an amended Form 4 (Form 4/A).'],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026'},
     'image_post': {'title': "At least 3 CEOs disclosed buying their own company's stock",
                    # news-v15: a row is one buyer's total (GameStop's is 2 purchases), never one purchase
                    'paragraphs': ["The image lists each buyer's total by company, role and amount: "
                                   "GameStop, CEO, $74.4M; Lowe's, CEO, $2.3M; Starbucks, CEO, $410K.",
                                   'Some of the shares in these purchases are reported as held '
                                   'indirectly. The figures include an amended Form 4 (Form 4/A).',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026',
     'persons': ['Ryan Cohen'],
     'logo_refs': [{'key': 'GME', 'name': 'GameStop'},
                   {'key': 'LOW', 'name': "Lowe's"},
                   {'key': 'SBUX', 'name': 'Starbucks'}],
     'source_ref': 'news:ceo_buys:2026-11-09',
     'x': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     # news-v20: format-neutral — the caption also rides a text post (no picture), where nothing is "shown"
     'linkedin': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov "
                 '9-15. The largest of these: GameStop, $74.4 million.\n'
                 '\n'
                 "The purchases: GameStop, CEO Ryan Cohen, $74.4 million; Lowe's, CEO, $2.3 "
                 'million; Starbucks, CEO, $410,000.\n'
                 '\n'
                 # news-v12: a roundup's flag paragraph speaks of several buyers (no singular "buyer's")
                 'Some of the shares are reported as held indirectly. The figures include an amended '
                 'Form 4 (Form 4/A).\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business "
                 "days of a trade. Amounts are each filing's share count times the cost it reports for "
                 'each share.',
     'tiktok': "GameStop's CEO disclosed buying $74.4 million of the company's stock in Form 4s filed Nov "
               '10-11.\n'
               '\n'
               'The filings name Ryan Cohen, the CEO of GameStop.',
     'youtube_title': "GameStop's CEO disclosed buying $74.4M of the company's stock"},
    'ceo_roundup5': {'hook': "GameStop's chief executive disclosed buying $74.4 million of the company's stock.",
     'video_script': ["The filings are by the company's chief executive and were filed from November 10th to "
                      'November 11th.',
                      'They report 2 purchases, 3 million shares in total, for $74.4 million.',
                      'Officers and directors must file a Form 4 within two business days of a trade.',
                      "At least 4 more chief executives disclosed buying their own company's stock in the same "
                      'week.'],
     'cards': [{'title': 'Who filed', 'body': 'The CEO of GameStop'},
               {'title': 'The purchases', 'body': '2 purchases · 3M shares · $74.4M'},
               {'title': 'What a Form 4 is', 'body': "Filed within two business days of an insider's trade"},
               {'title': 'Same week', 'body': "At least 4 more CEOs disclosed buying their own company's stock"}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['GME'],
                      'headline': "At least 5 CEOs disclosed buying their own company's stock",
                      'list': {'rows': [{'logo': 'GME', 'chip': 'GME', 'cells': ['GameStop', 'CEO', '$74.4M']},
                                        {'logo': 'LOW', 'chip': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                        {'logo': 'INTC', 'chip': 'INTC', 'cells': ['Intel', 'CEO', '$1M']},
                                        {'logo': 'SBUX', 'chip': 'SBUX', 'cells': ['Starbucks', 'CEO', '$410K']},
                                        {'logo': 'NEE',
                                         'chip': 'NEE',
                                         'cells': ['NextEra Energy', 'CEO', '$265K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 5 CEOs disclosed buying their own company's stock",
                    'sections': [{'rows': [{'logo': 'GME', 'chip': 'GME', 'cells': ['GameStop', 'CEO', '$74.4M']},
                                           {'logo': 'LOW', 'chip': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                           {'logo': 'INTC', 'chip': 'INTC', 'cells': ['Intel', 'CEO', '$1M']},
                                           {'logo': 'SBUX', 'chip': 'SBUX', 'cells': ['Starbucks', 'CEO', '$410K']},
                                           {'logo': 'NEE',
                                            'chip': 'NEE',
                                            'cells': ['NextEra Energy', 'CEO', '$265K']}]}],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026'},
     'image_post': {'title': "At least 5 CEOs disclosed buying their own company's stock",
                    'paragraphs': ["The image lists each buyer's total by company, role and amount: GameStop, CEO, "
                                   "$74.4M; Lowe's, CEO, $2.3M; Intel, CEO, $1M; Starbucks, CEO, $410K; NextEra "
                                   'Energy, CEO, $265K.',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026',
     'persons': ['Ryan Cohen'],
     'logo_refs': [{'key': 'GME', 'name': 'GameStop'},
                   {'key': 'LOW', 'name': "Lowe's"},
                   {'key': 'INTC', 'name': 'Intel'},
                   {'key': 'SBUX', 'name': 'Starbucks'},
                   {'key': 'NEE', 'name': 'NextEra Energy'}],
     'source_ref': 'news:ceo_buys:2026-11-09',
     'x': "At least 5 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'bluesky': "At least 5 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'threads': "At least 5 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15. The largest "
                'of these: GameStop, $74.4 million.\n'
                '\n'
                'Some of the shares are reported as held indirectly. The figures include an amended Form 4 (Form '
                '4/A).',
     'linkedin': "At least 5 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15. The largest "
                 'of these: GameStop, $74.4 million.\n'
                 '\n'
                 "The purchases: GameStop, CEO Ryan Cohen, $74.4 million; Lowe's, CEO, $2.3 million; Intel, CEO, $1 "
                 'million; Starbucks, CEO, $410,000; NextEra Energy, CEO, $265,000.\n'
                 '\n'
                 'Some of the shares are reported as held indirectly. The figures include an amended Form 4 (Form '
                 '4/A).\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.",
     'tiktok': "GameStop's CEO disclosed buying $74.4 million of the company's stock in Form 4s filed Nov 10-11.\n"
               '\n'
               'The filings name Ryan Cohen, the CEO of GameStop.',
     'instagram': "GameStop's CEO disclosed buying $74.4 million of the company's stock in Form 4s filed Nov 10-11.\n"
                  '\n'
                  'The filings name Ryan Cohen, the CEO of GameStop.\n'
                  '\n'
                  "A Form 4 is the report a company's officers and directors file within two business days of a "
                  "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                  '\n'
                  "At least 4 more CEOs disclosed buying their own company's stock in Form 4s filed the same week.",
     'youtube_title': "GameStop's CEO disclosed buying $74.4M of the company's stock"},
    'ceo_roundup_of': {'hook': "The chief executive of Lowe's disclosed buying $2.3 million of the company's stock.",
     'video_script': ["The filing is by the company's chief executive and was filed on November 12th.",
                      'It reports one purchase of 9,812 shares, for $2.3 million.',
                      "The shares are reported as held indirectly, not in the chief executive's own name.",
                      "At least 2 more chief executives disclosed buying their own company's stock in the same "
                      'week.'],
     'cards': [{'title': 'Who filed', 'body': "The CEO of Lowe's"},
               {'title': 'The purchase', 'body': '1 purchase · 9,812 shares · $2.3M'},
               {'title': 'Held indirectly', 'body': "Not in the CEO's own name, per the filing"},
               {'title': 'Same week', 'body': "At least 2 more CEOs disclosed buying their own company's stock"}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['LOW'],
                      'headline': "At least 3 CEOs disclosed buying their own company's stock",
                      'list': {'rows': [{'logo': 'LOW', 'chip': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                        {'logo': 'SBUX', 'chip': 'SBUX', 'cells': ['Starbucks', 'CEO', '$410K']},
                                        {'logo': 'NEE',
                                         'chip': 'NEE',
                                         'cells': ['NextEra Energy', 'CEO', '$265K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 3 CEOs disclosed buying their own company's stock",
                    'sections': [{'rows': [{'logo': 'LOW', 'chip': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                           {'logo': 'SBUX', 'chip': 'SBUX', 'cells': ['Starbucks', 'CEO', '$410K']},
                                           {'logo': 'NEE',
                                            'chip': 'NEE',
                                            'cells': ['NextEra Energy', 'CEO', '$265K']}]}],
                    'notes': ['Some of the shares in these purchases are reported as held indirectly.',
                              'The figures include an amended Form 4 (Form 4/A).'],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026'},
     'image_post': {'title': "At least 3 CEOs disclosed buying their own company's stock",
                    'paragraphs': ["The image lists each buyer's total by company, role and amount: Lowe's, CEO, "
                                   '$2.3M; Starbucks, CEO, $410K; NextEra Energy, CEO, $265K.',
                                   'Some of the shares in these purchases are reported as held indirectly. The '
                                   'figures include an amended Form 4 (Form 4/A).',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026',
     'persons': [],
     'logo_refs': [{'key': 'LOW', 'name': "Lowe's"},
                   {'key': 'SBUX', 'name': 'Starbucks'},
                   {'key': 'NEE', 'name': 'NextEra Energy'}],
     'source_ref': 'news:ceo_buys:2026-11-09',
     'x': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'bluesky': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'threads': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15. The largest "
                "of these: Lowe's, $2.3 million.",
     'linkedin': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15. The largest "
                 "of these: Lowe's, $2.3 million.\n"
                 '\n'
                 "The purchases: Lowe's, CEO, $2.3 million; Starbucks, CEO, $410,000; NextEra Energy, CEO, "
                 '$265,000.\n'
                 '\n'
                 'Some of the shares are reported as held indirectly. The figures include an amended Form 4 (Form '
                 '4/A).\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.",
     'tiktok': "The CEO of Lowe's disclosed buying $2.3 million of the company's stock in a Form 4 filed Nov 12.\n"
               '\n'
               "A Form 4 is the report a company's officers and directors file within two business days of a trade. "
               "Amounts are each filing's share count times the cost it reports for each share.",
     'instagram': "The CEO of Lowe's disclosed buying $2.3 million of the company's stock in a Form 4 filed Nov 12.\n"
                  '\n'
                  "A Form 4 is the report a company's officers and directors file within two business days of a "
                  "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                  '\n'
                  "The shares are reported as held indirectly rather than in the buyer's own name.\n"
                  '\n'
                  "At least 2 more CEOs disclosed buying their own company's stock in Form 4s filed the same week.",
     'youtube_title': "The CEO of Lowe's disclosed buying $2.3M of the company's stock"},
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
                      'headline': "At least 3 company insiders disclosed buying their own company's stock",
                      'list': {'rows': [{'logo': 'NKE', 'chip': 'NKE', 'cells': ['Nike', 'CFO', '$1.3M']},
                                        {'logo': 'CTSO', 'chip': 'CTSO', 'cells': ['Contoso', 'Director', '$640K']},
                                        {'logo': 'HSY', 'chip': 'HSY', 'cells': ['Hershey', 'Director', '$150K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 3 company insiders disclosed buying their own company's stock",
                    'sections': [{'rows': [{'logo': 'NKE', 'chip': 'NKE', 'cells': ['Nike', 'CFO', '$1.3M']},
                                           {'logo': 'CTSO', 'chip': 'CTSO', 'cells': ['Contoso', 'Director', '$640K']},
                                           {'logo': 'HSY', 'chip': 'HSY',
                                            'cells': ['Hershey', 'Director', '$150K']}]}],
                    'notes': ['Some of the shares in these purchases are reported as held indirectly.'],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026'},
     'image_post': {'title': "At least 3 company insiders disclosed buying their own company's stock",
                    'paragraphs': ["The image lists each buyer's total by company, role and amount: Nike, "
                                   'CFO, $1.3M; Contoso, Director, $640K; Hershey, Director, $150K.',
                                   'Some of the shares in these purchases are reported as held indirectly.',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026',
     'persons': ['Anna Smith-Jones'],
     'logo_refs': [{'key': 'NKE', 'name': 'Nike'},
                   {'key': 'CTSO', 'name': 'Contoso'},
                   {'key': 'HSY', 'name': 'Hershey'}],
     'source_ref': 'news:insider_buys:2026-11-09',
     'x': "At least 3 company insiders disclosed buying their own company's stock in Form 4s filed Nov "
          '9-15.',
     'linkedin': "At least 3 company insiders disclosed buying their own company's stock in Form 4s "
                 'filed Nov 9-15. The largest of these: Nike, $1.3 million.\n'
                 '\n'
                 'The purchases: Nike, CFO, $1.3 million; Contoso, director Anna Smith-Jones, '
                 '$640,000; Hershey, director, $150,000.\n'
                 '\n'
                 'Some of the shares are reported as held indirectly.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business "
                 "days of a trade. Amounts are each filing's share count times the cost it reports for "
                 'each share.',
     'tiktok': "Nike's CFO disclosed buying $1.3 million of the company's stock in a Form 4 filed Nov 10.\n"
               '\n'
               "A Form 4 is the report a company's officers and directors file within two business "
               "days of a trade. Amounts are each filing's share count times the cost it reports for "
               'each share.',
     'youtube_title': "Nike's CFO disclosed buying $1.3M of the company's stock"},
    # news-v12: a late filing — its trade dates (Oct 14-17) are said in place of the two-business-day rule
    'insider_late': {'hook': "A director of Hershey disclosed buying $150,000 of the company's stock.",
     'video_script': ['The filer is a director of the company, and the filing date is November 12th.',
                      '2 purchases are reported, 812 shares in total, for $150,000.',
                      'The purchases are reported with trade dates from October 14th to October 17th.',
                      "At least 2 more company insiders disclosed buying their own company's stock in the same "
                      'week.'],
     'cards': [{'title': 'Who filed', 'body': 'A director of Hershey'},
               {'title': 'The purchases', 'body': '2 purchases · 812 shares · $150K'},
               {'title': 'Trade dates', 'body': 'Oct 14–17, 2026 · filed Nov 12, 2026'},
               {'title': 'Same week',
                'body': "At least 2 more company insiders disclosed buying their own company's stock"}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['HSY'],
                      'headline': "At least 3 company insiders disclosed buying their own company's stock",
                      'list': {'rows': [{'logo': 'HSY', 'chip': 'HSY', 'cells': ['Hershey', 'Director', '$150K']},
                                        {'logo': 'LTW', 'chip': 'LTW', 'cells': ['Litware', 'Director', '$120K']},
                                        {'logo': 'PRSW', 'chip': 'PRSW', 'cells': ['Proseware', 'CFO', '$110K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 3 company insiders disclosed buying their own company's stock",
                    'sections': [{'rows': [{'logo': 'HSY', 'chip': 'HSY', 'cells': ['Hershey', 'Director', '$150K']},
                                           {'logo': 'LTW', 'chip': 'LTW', 'cells': ['Litware', 'Director', '$120K']},
                                           {'logo': 'PRSW',
                                            'chip': 'PRSW',
                                            'cells': ['Proseware', 'CFO', '$110K']}]}],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026'},
     'image_post': {'title': "At least 3 company insiders disclosed buying their own company's stock",
                    'paragraphs': ["The image lists each buyer's total by company, role and amount: Hershey, "
                                   'Director, $150K; Litware, Director, $120K; Proseware, CFO, $110K.',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026',
     'persons': [],
     'logo_refs': [{'key': 'HSY', 'name': 'Hershey'},
                   {'key': 'LTW', 'name': 'Litware'},
                   {'key': 'PRSW', 'name': 'Proseware'}],
     'source_ref': 'news:insider_buys:2026-11-09',
     'x': "At least 3 company insiders disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'bluesky': "At least 3 company insiders disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'threads': "At least 3 company insiders disclosed buying their own company's stock in Form 4s filed Nov 9-15. "
                'The largest of these: Hershey, $150,000.',
     'linkedin': "At least 3 company insiders disclosed buying their own company's stock in Form 4s filed Nov 9-15. "
                 'The largest of these: Hershey, $150,000.\n'
                 '\n'
                 'The purchases: Hershey, director, $150,000; Litware, director, $120,000; Proseware, CFO, '
                 '$110,000.\n'
                 '\n'
                 "The filings give trade dates from Oct 14 to Nov 14. A Form 4 is the report a company's officers "
                 "and directors file on their own trades. Amounts are each filing's share count times the cost it "
                 'reports for each share.',
     'tiktok': "A director of Hershey disclosed buying $150,000 of the company's stock on Form 4, filed Nov 12.\n"
               '\n'
               'The purchases are reported with trade dates from Oct 14 to Oct 17. A Form 4 is the report a '
               "company's officers and directors file on their own trades. Amounts are each filing's share count "
               'times the cost it reports for each share.',
     'instagram': "A director of Hershey disclosed buying $150,000 of the company's stock on Form 4, filed Nov 12.\n"
                  '\n'
                  'The purchases are reported with trade dates from Oct 14 to Oct 17. A Form 4 is the report a '
                  "company's officers and directors file on their own trades. Amounts are each filing's share count "
                  'times the cost it reports for each share.\n'
                  '\n'
                  "At least 2 more company insiders disclosed buying their own company's stock in Form 4s filed the "
                  'same week.',
     'youtube_title': "A director of Hershey disclosed buying $150K of the company's stock"},
    'berkshire_13f': {'hook': "5 of the holding changes in Berkshire Hathaway's 13F: 3 new, 2 closed out.",
      'video_script': ["Compared with the quarter before, it reported new holdings of Constellation Brands, Domino's "
                       'Pizza and Lennar.',
                       'Compared with the quarter before, it also closed out its holdings of Citigroup and Nu '
                       'Holdings.',
                       "A 13F shows holdings at the quarter's end, not when shares changed hands.",
                       "A 13F is due about 45 days after a quarter's end."],
      'cards': [{'title': 'New holdings', 'body': "Constellation Brands, Domino's Pizza and Lennar"},
                {'title': 'Closed out', 'body': 'Citigroup and Nu Holdings'},
                {'title': 'What a 13F shows', 'body': "Holdings at the quarter's end, not trade dates"},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['BRK-B'],
                       'headline': "Berkshire Hathaway's latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'STZ',
                                          'chip': 'STZ',
                                          'cells': ['Constellation Brands', 'New holding', '+5.6M']},
                                         {'logo': 'DPZ',
                                          'chip': 'DPZ',
                                          'cells': ["Domino's Pizza", 'New holding', '+1.3M']},
                                         {'logo': 'LEN', 'chip': 'LEN', 'cells': ['Lennar', 'New holding', '+1.8M']}],
                                'more': '+2 more changes in the caption'},
                       'chip': 'BRK-B'},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Berkshire Hathaway's latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'STZ',
                                             'chip': 'STZ',
                                             'cells': ['Constellation Brands', 'New holding', '+5.6M']},
                                            {'logo': 'DPZ',
                                             'chip': 'DPZ',
                                             'cells': ["Domino's Pizza", 'New holding', '+1.3M']},
                                            {'logo': 'LEN',
                                             'chip': 'LEN',
                                             'cells': ['Lennar', 'New holding', '+1.8M']},
                                            {'logo': 'C', 'chip': 'C', 'cells': ['Citigroup', 'Closed out', '−14.6M']},
                                            {'logo': 'NU',
                                             'chip': 'NU',
                                             'cells': ['Nu Holdings', 'Closed out', '−40.2M']}]}],
                     'header': {'logo': 'BRK-B', 'name': 'Berkshire Hathaway', 'chip': 'BRK-B'},
                     'footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026'},
      'image_post': {'title': "Berkshire Hathaway's latest 13F",
                     'paragraphs': ["It lists 5 of the filing's holding changes from the quarter before: 3 new, 2 "
                                    'closed out. Share counts are rounded.',
                                    "Constellation Brands, a new holding of about 5.6M shares; Domino's Pizza, a new "
                                    'holding of about 1.3M shares; Lennar, a new holding of about 1.8M shares; '
                                    'Citigroup, closed out, from about 14.6M shares; Nu Holdings, closed out, from '
                                    'about 40.2M shares.',
                                    'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, 2026.']},
      'image_footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026',
      'persons': [],
      'logo_refs': [{'key': 'BRK-B', 'name': 'Berkshire Hathaway'},
                    {'key': 'STZ', 'name': 'Constellation Brands'},
                    {'key': 'DPZ', 'name': "Domino's Pizza"},
                    {'key': 'LEN', 'name': 'Lennar'},
                    {'key': 'C', 'name': 'Citigroup'},
                    {'key': 'NU', 'name': 'Nu Holdings'}],
      'source_ref': 'news:thirteen_f:0001067983:2026-Q3',
      'x': "5 of the holding changes in Berkshire Hathaway's latest 13F: 3 new, 2 closed out.",
      'linkedin': "5 of the holding changes in Berkshire Hathaway's latest 13F: 3 new, 2 closed out.\n"
                  '\n'
                  'Constellation Brands (STZ): a new holding of about 5.6 million shares.\n'
                  '\n'
                  "Domino's Pizza (DPZ): a new holding of about 1.3 million shares.\n"
                  '\n'
                  'Lennar (LEN): a new holding of about 1.8 million shares.\n'
                  '\n'
                  'Citigroup (C): closed out, from about 14.6 million shares the quarter before.\n'
                  '\n'
                  'Nu Holdings (NU): closed out, from about 40.2 million shares the quarter before.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "5 of the holding changes in Berkshire Hathaway's latest 13F: 3 new, 2 closed out.\n"
                '\n'
                'Citigroup (C): closed out, from about 14.6 million shares the quarter before.\n'
                '\n'
                'Nu Holdings (NU): closed out, from about 40.2 million shares the quarter before.',
      'youtube_title': "Berkshire Hathaway's latest 13F: 5 of its holding changes"},
    'pershing_13f': {'hook': "3 of Pershing Square Capital Management's holding changes: 1 increased, 1 reduced, 1 new.",
      'video_script': ['Compared with the quarter before, it increased its Uber holding.',
                       'Compared with the quarter before, it also reduced its Chipotle Mexican Grill holding.',
                       'Compared with the quarter before, it also reported a new CoreWeave holding.',
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'Increased', 'body': 'Uber'},
                {'title': 'Reduced', 'body': 'Chipotle Mexican Grill'},
                {'title': 'New holding', 'body': 'CoreWeave'},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['13F-FILER'],
                       'headline': "Pershing Square Capital Management's latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'UBER',
                                          'chip': 'UBER',
                                          'cells': ['Uber', 'Increased to 40M shares', '+9.8M']},
                                         {'logo': 'CMG',
                                          'chip': 'CMG',
                                          'cells': ['Chipotle Mexican Grill', 'Reduced to 20M shares', '−6M']},
                                         {'logo': 'CRWV',
                                          'chip': 'CRWV',
                                          'cells': ['CoreWeave', 'New holding', '+900K']}]}},
      'persons': [],
      'x': "3 of the holding changes in Pershing Square Capital Management's latest 13F: 1 increased, 1 reduced, 1 "
           'new.',
      'linkedin': "3 of the holding changes in Pershing Square Capital Management's latest 13F: 1 increased, 1 "
                  'reduced, 1 new.\n'
                  '\n'
                  'Uber (UBER): increased to 40 million shares, from 30.2 million the quarter before.\n'
                  '\n'
                  'Chipotle Mexican Grill (CMG): reduced to 20 million shares, from 26 million.\n'
                  '\n'
                  'CoreWeave (CRWV): a new holding of 900,000 shares.\n'
                  '\n'
                  'CoreWeave first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30; it was filed on Nov 12 and amended on Nov 14.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "3 of the holding changes in Pershing Square Capital Management's latest 13F: 1 increased, 1 reduced, "
                '1 new.\n'
                '\n'
                'Uber (UBER): increased to 40 million shares, from 30.2 million the quarter before.',
      'youtube_title': "Pershing Square Capital Management's latest 13F: 3 of its holding changes"},
    'costco_money_map': {'hook': 'For every $100 of revenue in fiscal 2025, Costco kept $2.94 as net income.',
     'video_script': ['Costco reported revenue of $275.2 billion in fiscal 2025.',
                      'The United States made up 73% of revenue, and Canada made up 14% of revenue.',
                      'After the cost of sales, gross profit was $35.4 billion; after operating costs, '
                      'operating profit was $10.4 billion.',
                      'After interest, taxes and everything else, net income was $8.1 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$275.2B in fiscal 2025'},
               {'title': 'Revenue mix', 'body': 'United States: 73% of revenue · Canada: 14% of revenue'},
               {'title': 'After costs', 'body': 'Gross profit $35.4B · operating profit $10.4B'},
               {'title': 'Net income', 'body': '$8.1B in fiscal 2025'}],
     'opening_card': {'kicker': 'MONEY MAP',
                      'logos': ['COST'],
                      'chip': 'COST',
                      'figure': '$2.94',
                      'figure_label': 'of every $100 of revenue was net income',
                      'headline': 'How Costco makes money'},
     'image_spec': {'layout': 'bars',
                    'version': 1,
                    'kicker': 'MONEY MAP',
                    'header': {'logo': 'COST', 'name': 'Costco', 'chip': 'COST'},     # news-v23: the plate's pill
                    'title': 'How Costco makes money',
                    'subtitle': 'Fiscal 2025 · annual results',
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
                    'footer': 'Not investment advice · Source: company financial statements · fiscal 2025'},
     'image_post': {'title': 'How Costco makes money',
                    'paragraphs': ['Bars show revenue by region: United States $200B; Canada $38.5B; '
                                   'Other International $36.7B.',
                                   'A second set of bars shows Revenue $275.2B; Gross profit $35.4B; '
                                   'Operating profit $10.4B; Net income $8.1B.',
                                   'For every $100 of revenue, $2.94 was net income.',
                                   'Source: company financial statements. Fiscal 2025, ended Aug 31, '
                                   '2025.']},
     'image_footer': 'Not investment advice · Source: company financial statements · fiscal 2025',
     'persons': [],
     'logo_refs': [{'key': 'COST', 'name': 'Costco'}],
     'source_ref': 'news:money_map:COST:2025',
     'x': 'How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every $100 '
          'kept as net income.',
     'linkedin': 'How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every '
                 '$100 kept as net income.\n'
                 '\n'
                 'How fiscal 2025 revenue broke down by region: the United States, $200 billion; Canada, $38.5 '
                 'billion; Other International, $36.7 billion.\n'
                 '\n'
                 'After the cost of sales, gross profit was $35.4 billion. After operating costs, '
                 'operating profit was $10.4 billion.\n'
                 '\n'
                 'Net income was $8.1 billion: $2.94 of every $100 of revenue.\n'
                 '\n'
                 "Figures come from the company's annual financial statements for fiscal 2025. Gross profit "
                 'is revenue minus the cost of sales.',
     'tiktok': 'How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every '
               '$100 kept as net income.\n'
               '\n'
               'How fiscal 2025 revenue broke down by region: the United States, $200 billion; Canada, $38.5 '
               'billion; Other International, $36.7 billion.',
     'youtube_title': 'How Costco makes money: fiscal 2025 results'},
    'rivian_money_map': {'hook': 'Rivian Automotive had a net loss of $66.38 for every $100 of revenue.',
     'video_script': ['Rivian Automotive reported revenue of $5.8 billion in fiscal 2025.',
                      'Automotive made up 80% of revenue, and Software and services made up 20% of '
                      'revenue.',
                      'After the cost of sales, gross loss was $80 million; after operating costs, '
                      'operating loss was $4 billion.',
                      'After interest, taxes and everything else, the net loss was $3.9 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$5.8B in fiscal 2025'},
               {'title': 'Revenue mix',
                'body': 'Automotive: 80% of revenue · Software and services: 20% of revenue'},
               {'title': 'After costs', 'body': 'Gross loss $80M · operating loss $4B'},
               {'title': 'Net loss', 'body': '$3.9B in fiscal 2025'}],
     # owner decision 2026-11-20 ("A: costs vs revenue"): a loss year's cover is its COSTS card under the sub line (the
     # figure and its label stay: the fallback card) — re-pinned on purpose; the captions and narration did not move
     'opening_card': {'kicker': 'MONEY MAP',
                      'logos': ['RIVN'],
                      'chip': 'RIVN',
                      'figure': '$66.38',
                      'figure_label': 'net loss for every $100 of revenue',
                      'headline': 'How Rivian Automotive makes money',
                      'subtitle': 'Fiscal 2025 · annual results',
                      'costs': {'revenue': {'label': 'Revenue', 'value': '$5.8B', 'ratio': 0.5943},
                                'net': {'label': 'Net loss', 'value': '$3.9B'},
                                'total': {'label': 'Costs and expenses', 'value': '$9.7B', 'ratio': 0.9887},
                                'parts': [{'label': 'Cost of sales', 'value': '$5.9B', 'ratio': 0.6025},
                                          {'label': 'Operating expenses', 'value': '$3.9B', 'ratio': 0.3975}],
                                'note': '+$110M from interest, tax and other items made the loss smaller',
                                'callout': ['For every $100 of revenue, costs were $166.38,',
                                            'so the net loss was $66.38']}},
     'persons': [],
     'x': 'How Rivian Automotive makes money: $5.8 billion of revenue in fiscal 2025, and a net loss '
          'of $66.38 for every $100.',
     'linkedin': 'How Rivian Automotive makes money: $5.8 billion of revenue in fiscal 2025, and a net '
                 'loss of $66.38 for every $100.\n'
                 '\n'
                 'How fiscal 2025 revenue broke down: Automotive, $4.6 billion; Software and services, '
                 '$1.2 billion.\n'
                 '\n'
                 'After the cost of sales, gross loss was $80 million. After operating costs, '
                 'operating loss was $4 billion.\n'
                 '\n'
                 'The net loss was $3.9 billion: $66.38 for every $100 of revenue.\n'
                 '\n'
                 # the review of 2026-10-16 (MM-5): a gross LOSS is explained by its own twin
                 "Figures come from the company's annual financial statements for fiscal 2025. Gross profit "
                 'or loss is revenue minus the cost of sales.',
     'tiktok': 'How Rivian Automotive makes money: $5.8 billion of revenue in fiscal 2025, and a net '
               'loss of $66.38 for every $100.\n'
               '\n'
               'How fiscal 2025 revenue broke down: Automotive, $4.6 billion; Software and services, '
               '$1.2 billion.',
     'youtube_title': 'How Rivian Automotive makes money: fiscal 2025 results'},
    'comcast_money_map': {'hook': 'For every $100 of revenue in fiscal 2025, Comcast kept $13.10 as net income.',
     'video_script': ['Comcast reported revenue of $123.7 billion in fiscal 2025.',
                      # news-v19: without "Its … business" the two-part line fits LINE_WORDS (19 words)
                      'Connectivity and Platforms made up 64% of combined sales, and Content and '
                      'Experiences made up 34% of combined sales.',
                      'After all operating costs, operating profit was $23.3 billion that year.',
                      'After interest, taxes and everything else, net income was $16.2 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$123.7B in fiscal 2025'},
               {'title': 'Revenue mix',
                'body': 'Connectivity and Platforms: 64% of combined sales · Content and Experiences: '
                        '34% of combined sales'},
               {'title': 'After costs', 'body': 'Operating profit $23.3B'},
               {'title': 'Net income', 'body': '$16.2B in fiscal 2025'}],
     'opening_card': {'kicker': 'MONEY MAP',
                      'logos': ['CMCSA'],
                      'chip': 'CMCSA',
                      'figure': '$13.10',
                      'figure_label': 'of every $100 of revenue was net income',
                      'headline': 'How Comcast makes money'},
     'persons': [],
     'x': 'How Comcast makes money: $123.7 billion of revenue in fiscal 2025, and $13.10 of every $100 '
          'kept as net income.',
     'linkedin': 'How Comcast makes money: $123.7 billion of revenue in fiscal 2025, and $13.10 of '
                 'every $100 kept as net income.\n'
                 '\n'
                 'How fiscal 2025 revenue broke down: Connectivity and Platforms, $82 billion; Content '
                 'and Experiences, $43.4 billion; everything else, $2.6 billion. Sales between parts of the '
                 'company, $4.3 billion, are taken out of that total.\n'
                 '\n'
                 'After operating costs, operating profit was $23.3 billion.\n'
                 '\n'
                 'Net income was $16.2 billion: $13.10 of every $100 of revenue.\n'
                 '\n'
                 "Figures come from the company's annual financial statements for fiscal 2025.",
     'tiktok': 'How Comcast makes money: $123.7 billion of revenue in fiscal 2025, and $13.10 of every '
               '$100 kept as net income.\n'
               '\n'
               'How fiscal 2025 revenue broke down: Connectivity and Platforms, $82 billion; Content '
               'and Experiences, $43.4 billion; everything else, $2.6 billion. Sales between parts of the '
               'company, $4.3 billion, are taken out of that total.',
     'youtube_title': 'How Comcast makes money: fiscal 2025 results'},
    # news-v19 (Batch 1, plan item 2): an all-regional map reads "by region" (Costco, above); a mixed one
    # (McDonald's markets) the neutral wording, "the United States" in running text only; Amazon keeps AWS and
    # explains its FMP-filled gross profit. The review of 2026-10-16: Coca-Cola's parts are all regions, but its
    # rest ($8.4B, 18% of revenue — 22% live, mostly businesses) is over MONEY_MAP_REGION_MAX_OTHER_SHARE, so it
    # reads the neutral wording too (MM-6); the rest is "Everything else" (F6); McDonald's retired "High-Growth
    # Markets" label is its International Operated Markets (MM-1).
    'coca_cola_money_map': {'hook': 'For every $100 of revenue in fiscal 2025, Coca-Cola kept $25.31 as net income.',
     'video_script': ['Coca-Cola reported revenue of $47.8 billion in fiscal 2025.',
                      'North America made up 40% of revenue, and Europe, Middle East and Africa made up 17% of '
                      'revenue.',
                      'After the cost of sales, gross profit was $29.3 billion; after operating costs, '
                      'operating profit was $13.1 billion.',
                      'After interest, taxes and everything else, net income was $12.1 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$47.8B in fiscal 2025'},
               {'title': 'Revenue mix',
                'body': 'North America: 40% of revenue · Europe, Middle East and Africa: 17% of revenue'},
               {'title': 'After costs', 'body': 'Gross profit $29.3B · operating profit $13.1B'},
               {'title': 'Net income', 'body': '$12.1B in fiscal 2025'}],
     'image_post': {'title': 'How Coca-Cola makes money',
                    'paragraphs': ['Bars show how revenue broke down: North America $19.2B; Europe, Middle East '
                                   'and Africa $8.1B; Latin America $6.9B; Asia Pacific $5.2B; Everything else '
                                   '$8.4B.',
                                   'A second set of bars shows Revenue $47.8B; Gross profit $29.3B; Operating '
                                   'profit $13.1B; Net income $12.1B.',
                                   'For every $100 of revenue, $25.31 was net income.',
                                   'Source: company financial statements. Fiscal 2025, ended Dec 31, 2025.']},
     'persons': [],
     'source_ref': 'news:money_map:KO:2025',
     'x': 'How Coca-Cola makes money: $47.8 billion of revenue in fiscal 2025, and $25.31 of every $100 kept '
          'as net income.',
     'linkedin': 'How Coca-Cola makes money: $47.8 billion of revenue in fiscal 2025, and $25.31 of every $100 '
                 'kept as net income.\n'
                 '\n'
                 'How fiscal 2025 revenue broke down: North America, $19.2 billion; Europe, Middle '
                 'East and Africa, $8.1 billion; Latin America, $6.9 billion; Asia Pacific, $5.2 billion; '
                 'everything else, $8.4 billion.\n'
                 '\n'
                 'After the cost of sales, gross profit was $29.3 billion. After operating costs, operating '
                 'profit was $13.1 billion.\n'
                 '\n'
                 'Net income was $12.1 billion: $25.31 of every $100 of revenue.\n'
                 '\n'
                 "Figures come from the company's annual financial statements for fiscal 2025. Gross profit is "
                 'revenue minus the cost of sales.',
     'tiktok': 'How Coca-Cola makes money: $47.8 billion of revenue in fiscal 2025, and $25.31 of every $100 '
               'kept as net income.\n'
               '\n'
               'How fiscal 2025 revenue broke down: North America, $19.2 billion; Europe, Middle '
               'East and Africa, $8.1 billion; Latin America, $6.9 billion; Asia Pacific, $5.2 billion; everything '
               'else, $8.4 billion.',
     'youtube_title': 'How Coca-Cola makes money: fiscal 2025 results'},
    'mcdonalds_money_map': {'hook': "For every $100 of revenue in fiscal 2025, McDonald's kept $31.62 as net income.",
     'video_script': ["McDonald's reported revenue of $26.9 billion in fiscal 2025.",
                      'International Operated Markets made up 51% of revenue, and the United States made up 40% '
                      'of revenue.',
                      'After the cost of sales, gross profit was $15.1 billion; after operating costs, '
                      'operating profit was $12.3 billion.',
                      'After interest, taxes and everything else, net income was $8.5 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$26.9B in fiscal 2025'},
               {'title': 'Revenue mix',
                'body': 'International Operated Markets: 51% of revenue · United States: 40% of revenue'},
               {'title': 'After costs', 'body': 'Gross profit $15.1B · operating profit $12.3B'},
               {'title': 'Net income', 'body': '$8.5B in fiscal 2025'}],
     'image_post': {'title': "How McDonald's makes money",
                    'paragraphs': ['Bars show how revenue broke down: International Operated Markets $13.6B; '
                                   'United States $10.8B; Everything else $2.4B.',
                                   'A second set of bars shows Revenue $26.9B; Gross profit $15.1B; Operating '
                                   'profit $12.3B; Net income $8.5B.',
                                   'For every $100 of revenue, $31.62 was net income.',
                                   'Source: company financial statements. Fiscal 2025, ended Dec 31, 2025.']},
     'persons': [],
     'source_ref': 'news:money_map:MCD:2025',
     'x': "How McDonald's makes money: $26.9 billion of revenue in fiscal 2025, and $31.62 of every $100 kept "
          'as net income.',
     'linkedin': "How McDonald's makes money: $26.9 billion of revenue in fiscal 2025, and $31.62 of every "
                 '$100 kept as net income.\n'
                 '\n'
                 'How fiscal 2025 revenue broke down: International Operated Markets, $13.6 billion; the United '
                 'States, $10.8 billion; everything else, $2.4 billion.\n'
                 '\n'
                 'After the cost of sales, gross profit was $15.1 billion. After operating costs, operating '
                 'profit was $12.3 billion.\n'
                 '\n'
                 'Net income was $8.5 billion: $31.62 of every $100 of revenue.\n'
                 '\n'
                 "Figures come from the company's annual financial statements for fiscal 2025. Gross profit is "
                 'revenue minus the cost of sales.',
     'tiktok': "How McDonald's makes money: $26.9 billion of revenue in fiscal 2025, and $31.62 of every $100 "
               'kept as net income.\n'
               '\n'
               'How fiscal 2025 revenue broke down: International Operated Markets, $13.6 billion; the United '
               'States, $10.8 billion; everything else, $2.4 billion.',
     'youtube_title': "How McDonald's makes money: fiscal 2025 results"},
    'amazon_money_map': {'hook': 'For every $100 of revenue in fiscal 2025, Amazon kept $10.74 as net income.',
     'video_script': ['Amazon reported revenue of $717 billion in fiscal 2025.',
                      'Online Stores made up 38% of revenue, and Third-Party Seller Services made up 24% of '
                      'revenue.',
                      'After the cost of sales, gross profit was $360 billion; after operating costs, '
                      'operating profit was $80 billion.',
                      'After interest, taxes and everything else, net income was $77 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$717B in fiscal 2025'},
               {'title': 'Revenue mix',
                'body': 'Online Stores: 38% of revenue · Third-Party Seller Services: 24% of revenue'},
               {'title': 'After costs', 'body': 'Gross profit $360B · operating profit $80B'},
               {'title': 'Net income', 'body': '$77B in fiscal 2025'}],
     'image_post': {'title': 'How Amazon makes money',
                    'paragraphs': ['Bars show how revenue broke down: Online Stores $270B; Third-Party Seller '
                                   'Services $170B; AWS $128B; Advertising Services $69B; Subscription '
                                   'Services $49B; Everything else $31B.',
                                   'A second set of bars shows Revenue $717B; Gross profit $360B; Operating '
                                   'profit $80B; Net income $77B.',
                                   'For every $100 of revenue, $10.74 was net income.',
                                   'Source: company financial statements. Fiscal 2025, ended Dec 31, 2025.']},
     'persons': [],
     'source_ref': 'news:money_map:AMZN:2025',
     'x': 'How Amazon makes money: $717 billion of revenue in fiscal 2025, and $10.74 of every $100 kept as '
          'net income.',
     'linkedin': 'How Amazon makes money: $717 billion of revenue in fiscal 2025, and $10.74 of every $100 '
                 'kept as net income.\n'
                 '\n'
                 'How fiscal 2025 revenue broke down: Online Stores, $270 billion; Third-Party Seller '
                 'Services, $170 billion; AWS, $128 billion; Advertising Services, $69 billion; Subscription '
                 'Services, $49 billion; everything else, $31 billion.\n'
                 '\n'
                 'After the cost of sales, gross profit was $360 billion. After operating costs, operating '
                 'profit was $80 billion.\n'
                 '\n'
                 'Net income was $77 billion: $10.74 of every $100 of revenue.\n'
                 '\n'
                 "Figures come from the company's annual financial statements for fiscal 2025. Gross profit is "
                 'revenue minus the cost of sales.',
     'tiktok': 'How Amazon makes money: $717 billion of revenue in fiscal 2025, and $10.74 of every $100 kept '
               'as net income.\n'
               '\n'
               'How fiscal 2025 revenue broke down: Online Stores, $270 billion; Third-Party Seller Services, '
               '$170 billion; AWS, $128 billion; Advertising Services, $69 billion; Subscription Services, $49 '
               'billion; everything else, $31 billion.',
     'youtube_title': 'How Amazon makes money: fiscal 2025 results'},
}


#: Drop 2b goldens (news-v9; earnings_loss's LinkedIn / TikTok captions news-v10; the LinkedIn captions of
#: congress_accenture, every stake_* and both earnings_* news-v11; earnings_loss's narration, cards, picture,
#: alt text and LinkedIn / TikTok captions news-v21 — no revenue; earnings_loss's first card news-v23's visual-fidelity
#: round, 2026-11-20: a drawn card's per-share figures carry U+2212, the captions keep "-$0.05"): one per 2b sample,
#: pinned verbatim — regenerate only with a TEMPLATE_VERSION bump, after reading every string.
GOLDEN_2B = {
    'congress_accenture': {'hook': '3 members of Congress disclosed purchases of Accenture stock in November 2026.',
      'video_script': ['2 members disclosed purchases of Contoso stock, and 2 of Northwind stock.',
                       'The counts cover periodic transaction reports disclosed during November 2026, as of December '
                       '8th.',
                       "Each member is counted once per company; reports can include a spouse's or child's trades.",
                       'Reports are due within 45 days of a trade; some come later, so the trades may be older.'],
      'cards': [{'title': 'Also disclosed', 'body': 'Contoso: 2 members · Northwind: 2 members'},
                {'title': 'Disclosed in', 'body': 'November 2026 · as of Dec 8, 2026'},
                {'title': 'Counted once per company', 'body': 'Can include spouse and dependent-child trades'},
                {'title': 'Reporting delay', 'body': 'Due within 45 days of the trade; some are filed later'}],
      'opening_card': {'kicker': 'CONGRESS · DISCLOSED IN NOVEMBER',
                       'logos': ['ACN'],
                       'headline': 'Members of Congress disclosed purchases',
                       'list': {'rows': [{'logo': 'ACN',
                                          'chip': 'ACN',
                                          'cells': ['Accenture',
                                                    'members disclosed purchases of Accenture stock',
                                                    '3']},
                                         {'logo': 'CTSO',
                                          'chip': 'CTSO',
                                          'cells': ['Contoso', 'members disclosed purchases of Contoso stock', '2']},
                                         {'logo': 'NWT',
                                          'chip': 'NWT',
                                          'cells': ['Northwind',
                                                    'members disclosed purchases of Northwind stock',
                                                    '2']}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'CONGRESS · DISCLOSED IN NOVEMBER',
                     'title': 'Members of Congress disclosed purchases',
                     'subtitle': 'November 2026 · disclosures as of Dec 8, 2026',
                     'sections': [{'rows': [{'logo': 'ACN',
                                             'chip': 'ACN',
                                             'cells': ['Accenture',
                                                       'members disclosed purchases of Accenture stock',
                                                       '3']},
                                            {'logo': 'CTSO',
                                             'chip': 'CTSO',
                                             'cells': ['Contoso',
                                                       'members disclosed purchases of Contoso stock',
                                                       '2']},
                                            {'logo': 'NWT',
                                             'chip': 'NWT',
                                             'cells': ['Northwind',
                                                       'members disclosed purchases of Northwind stock',
                                                       '2']}]}],
                     'notes': ['Each member counted once per company'],
                     'footer': 'Not investment advice · Source: congressional disclosures · November 2026'},
      'image_post': {'title': 'Members of Congress disclosed purchases',
                     'paragraphs': ['The image shows, for 3 companies, how many members of Congress disclosed '
                                    "purchases of each company's stock in November 2026, each member counted once per "
                                    'company.',
                                    'Accenture, 3 members; Contoso, 2 members; Northwind, 2 members.',
                                    'Source: congressional periodic transaction reports. November 2026 · as of Dec 8, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: congressional disclosures · November 2026',
      'persons': [],
      'logo_refs': [{'key': 'ACN', 'name': 'Accenture'},
                    {'key': 'CTSO', 'name': 'Contoso'},
                    {'key': 'NWT', 'name': 'Northwind'}],
      'source_ref': 'news:congress_count:2026-11',
      'x': '3 members of Congress disclosed purchases of Accenture stock in November 2026, 2 of Contoso and 2 of '
           'Northwind.',
      'bluesky': 'Members of Congress disclosed purchases of Accenture, Contoso and Northwind stock in November 2026.',
      'linkedin': '3 members of Congress disclosed purchases of Accenture stock in November 2026, 2 of Contoso and 2 '
                  'of Northwind.\n'
                  '\n'
                  'The counts cover periodic transaction reports disclosed during November 2026, as of Dec 8.\n'
                  '\n'
                  'Each member is counted once per company, however many reports they filed that month.\n'
                  '\n'
                  "These reports can include a spouse's or dependent child's trades, and give amounts only as "
                  'ranges.\n'
                  '\n'
                  'Reports are due within 45 days of a trade; some come later, so the trades may be older.',
      'tiktok': '3 members of Congress disclosed purchases of Accenture stock in November 2026, 2 of Contoso and 2 of '
                'Northwind.\n'
                '\n'
                'The counts cover periodic transaction reports disclosed during November 2026, as of Dec 8.',
      'youtube_title': '3 members of Congress disclosed purchases of Accenture stock in Nov 2026'},
    # news-v23 (the restyle, owner 2026-11-19: "filter for companies that public only. no private" + "add 1-3
    # more companies for the NVIDIA"): one investor's stakes in public companies — a `rows` list, the cover the
    # same list; a private / non-U.S. stake is never a row (the one-stake `pair` samples are gone)
    'stakes_nvidia': {'hook': "Nvidia's stakes in 3 public companies, each with its disclosed figure.",
      'video_script': ['Nvidia invested $5 billion in Intel, as of December 26th, 2025.',
                       'Also on the list: $2 billion invested in Synopsys, and $2 billion invested in Coherent.',
                       'Each figure is the amount its source reports as invested, not a figure for today.',
                       'Each of these companies is listed on a U.S. exchange.'],
      'cards': [{'title': 'Intel', 'body': 'Invested $5B · as of Dec 26, 2025'},
                {'title': 'Also on the list', 'body': 'Synopsys · $2B invested; Coherent · $2B invested'},
                {'title': 'Invested', 'body': 'The amount put in, not a figure for today'},
                {'title': 'Public companies', 'body': 'Each one is listed on a U.S. exchange'}],
      'opening_card': {'kicker': 'COMPANY STAKES',
                       'logos': ['NVDA'],
                       'chip': 'NVDA',
                       'headline': "NVIDIA's stakes in public companies",
                       'list': {'rows': [{'logo': 'INTC',
                                          'chip': 'INTC',
                                          'cells': ['Intel', 'As of Dec 26, 2025 · Intel 8-K (Dec 29, 2025)', '$5B'],
                                          'label': 'invested'},
                                         {'logo': 'SNPS',
                                          'chip': 'SNPS',
                                          'cells': ['Synopsys', 'As of Dec 1, 2025 · Synopsys 8-K', '$2B'],
                                          'label': 'invested'},
                                         {'logo': 'COHR',
                                          'chip': 'COHR',
                                          'cells': ['Coherent', 'As of Mar 2, 2026 · Coherent 8-K', '$2B'],
                                          'label': 'invested'}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'COMPANY STAKES',
                     'header': {'logo': 'NVDA', 'name': 'NVIDIA', 'chip': 'NVDA'},
                     'title': "NVIDIA's stakes in public companies",
                     'sections': [{'rows': [{'logo': 'INTC',
                                             'chip': 'INTC',
                                             'cells': ['Intel',
                                                       'As of Dec 26, 2025 · Intel 8-K (Dec 29, 2025)',
                                                       '$5B'],
                                             'label': 'invested'},
                                            {'logo': 'SNPS',
                                             'chip': 'SNPS',
                                             'cells': ['Synopsys', 'As of Dec 1, 2025 · Synopsys 8-K', '$2B'],
                                             'label': 'invested'},
                                            {'logo': 'COHR',
                                             'chip': 'COHR',
                                             'cells': ['Coherent', 'As of Mar 2, 2026 · Coherent 8-K', '$2B'],
                                             'label': 'invested'}]}],
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': "NVIDIA's stakes in public companies",
                     'paragraphs': ['The image lists 3 stakes of NVIDIA in public companies, each with its figure, '
                                    'basis, date and source.',
                                    'Intel, $5B invested; Synopsys, $2B invested; Coherent, $2B invested.',
                                    'Source: company filings and official releases. As of Dec 1, 2025 to Mar 2, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
      'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'},
                    {'key': 'INTC', 'name': 'Intel'},
                    {'key': 'SNPS', 'name': 'Synopsys'},
                    {'key': 'COHR', 'name': 'Coherent'}],
      'source_ref': 'news:company_stakes:4e6a8c0e-2b4d-4c6e-8a0c-2e4a6c8e0a06+5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01+0d3b6f1e-2a4c-4e8b-9f10-6c2d8a7e5b02',
      'x': "NVIDIA's stakes in 3 public companies, each with its source and date.\n"
           '\n'
           'Intel: $5 billion invested, as of Dec 26, 2025.',
      'threads': "NVIDIA's stakes in 3 public companies, each with its source and date.\n"
                 '\n'
                 'Intel: $5 billion invested, as of Dec 26, 2025.\n'
                 '\n'
                 'Synopsys: $2 billion invested, as of Dec 1, 2025.\n'
                 '\n'
                 'Coherent: $2 billion invested, as of Mar 2.',
      'facebook': "NVIDIA's stakes in 3 public companies, each with its source and date.\n"
                  '\n'
                  'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).\n'
                  '\n'
                  'NVIDIA invested $2 billion in Synopsys, as of Dec 1, 2025. Source: Synopsys 8-K (Dec 1, 2025).\n'
                  '\n'
                  'NVIDIA invested $2 billion in Coherent, as of Mar 2. Source: Coherent 8-K (Mar 2, 2026).\n'
                  '\n'
                  'Each figure is the amount its source reports as invested, not a figure for today.\n'
                  '\n'
                  'Each of these companies is listed on a U.S. exchange.\n'
                  '\n'
                  'Companies disclose stakes like this in filings and official releases.',
      'tiktok': "NVIDIA's stakes in 3 public companies, each with its source and date.\n"
                '\n'
                'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).',
      'youtube_title': "NVIDIA's stakes in 3 public companies"},
    'stakes_nvidia_more': {'hook': "Nvidia's stakes in 4 public companies, each with its disclosed figure.",
      'video_script': ['Nvidia invested $5 billion in Intel, as of December 26th, 2025.',
                       'Also on the list: $2 billion invested in Synopsys, and $2 billion invested in Coherent.',
                       'Each figure is the amount its source reports as invested, not a figure for today.',
                       'Each of these companies is listed on a U.S. exchange.'],
      'cards': [{'title': 'Intel', 'body': 'Invested $5B · as of Dec 26, 2025'},
                {'title': 'Also on the list', 'body': 'Synopsys · $2B invested; Coherent · $2B invested'},
                {'title': 'Invested', 'body': 'The amount put in, not a figure for today'},
                {'title': 'Public companies', 'body': 'Each one is listed on a U.S. exchange'}],
      'opening_card': {'kicker': 'COMPANY STAKES',
                       'logos': ['NVDA'],
                       'chip': 'NVDA',
                       'headline': "NVIDIA's stakes in public companies",
                       'list': {'rows': [{'logo': 'INTC',
                                          'chip': 'INTC',
                                          'cells': ['Intel', 'As of Dec 26, 2025 · Intel 8-K (Dec 29, 2025)', '$5B'],
                                          'label': 'invested'},
                                         {'logo': 'SNPS',
                                          'chip': 'SNPS',
                                          'cells': ['Synopsys', 'As of Dec 1, 2025 · Synopsys 8-K', '$2B'],
                                          'label': 'invested'},
                                         {'logo': 'COHR',
                                          'chip': 'COHR',
                                          'cells': ['Coherent', 'As of Mar 2, 2026 · Coherent 8-K', '$2B'],
                                          'label': 'invested'}],
                                'more': '+1 more stake in the caption'}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'COMPANY STAKES',
                     'header': {'logo': 'NVDA', 'name': 'NVIDIA', 'chip': 'NVDA'},
                     'title': "NVIDIA's stakes in public companies",
                     'sections': [{'rows': [{'logo': 'INTC',
                                             'chip': 'INTC',
                                             'cells': ['Intel',
                                                       'As of Dec 26, 2025 · Intel 8-K (Dec 29, 2025)',
                                                       '$5B'],
                                             'label': 'invested'},
                                            {'logo': 'SNPS',
                                             'chip': 'SNPS',
                                             'cells': ['Synopsys', 'As of Dec 1, 2025 · Synopsys 8-K', '$2B'],
                                             'label': 'invested'},
                                            {'logo': 'COHR',
                                             'chip': 'COHR',
                                             'cells': ['Coherent', 'As of Mar 2, 2026 · Coherent 8-K', '$2B'],
                                             'label': 'invested'},
                                            {'logo': 'GLW',
                                             'chip': 'GLW',
                                             'cells': ['Corning', 'As of Jun 30, 2026 · Corning 10-Q', '$500M'],
                                             'label': 'invested'}]}],
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': "NVIDIA's stakes in public companies",
                     'paragraphs': ['The image lists 4 stakes of NVIDIA in public companies, each with its figure, '
                                    'basis, date and source.',
                                    'Intel, $5B invested; Synopsys, $2B invested; Coherent, $2B invested; Corning, '
                                    '$500M invested.',
                                    'Source: company filings and official releases. As of Dec 1, 2025 to Jun 30, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
      'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'},
                    {'key': 'INTC', 'name': 'Intel'},
                    {'key': 'SNPS', 'name': 'Synopsys'},
                    {'key': 'COHR', 'name': 'Coherent'},
                    {'key': 'GLW', 'name': 'Corning'}],
      'source_ref': 'news:company_stakes:4e6a8c0e-2b4d-4c6e-8a0c-2e4a6c8e0a06+5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01+0d3b6f1e-2a4c-4e8b-9f10-6c2d8a7e5b02+2c4e6a8b-0d1f-4a3c-9e5b-7d9f1b3d5f04',
      'x': "NVIDIA's stakes in 4 public companies, each with its source and date.\n"
           '\n'
           'Intel: $5 billion invested, as of Dec 26, 2025.',
      'threads': "NVIDIA's stakes in 4 public companies, each with its source and date.\n"
                 '\n'
                 'Corning: $500 million invested, as of Jun 30.',
      'facebook': "NVIDIA's stakes in 4 public companies, each with its source and date.\n"
                  '\n'
                  'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).\n'
                  '\n'
                  'NVIDIA invested $2 billion in Synopsys, as of Dec 1, 2025. Source: Synopsys 8-K (Dec 1, 2025).\n'
                  '\n'
                  'NVIDIA invested $2 billion in Coherent, as of Mar 2. Source: Coherent 8-K (Mar 2, 2026).\n'
                  '\n'
                  'NVIDIA invested $500 million in Corning, as of Jun 30. Source: Corning 10-Q (quarter ended Jun 30, '
                  '2026).\n'
                  '\n'
                  'Each figure is the amount its source reports as invested, not a figure for today.\n'
                  '\n'
                  'Each of these companies is listed on a U.S. exchange.\n'
                  '\n'
                  'Companies disclose stakes like this in filings and official releases.',
      'tiktok': "NVIDIA's stakes in 4 public companies, each with its source and date.\n"
                '\n'
                'NVIDIA invested $500 million in Corning, as of Jun 30. Source: Corning 10-Q (quarter ended Jun 30, '
                '2026).',
      'youtube_title': "NVIDIA's stakes in 4 public companies"},
    'stakes_berkshire_mixed': {'hook': "Berkshire Hathaway's stakes in 2 public companies, each with its disclosed figure.",
      'video_script': ["Berkshire Hathaway's Litware stake had a fair value of $8.9 billion, as of December 31st, "
                       '2025.',
                       'Also on the list: a stake in Fabrikam carried at $461 million.',
                       'Each figure is shown with its own basis and date, from the source named beside it.',
                       'Each of these companies is listed on a U.S. exchange.'],
      'cards': [{'title': 'Litware', 'body': 'Fair value $8.9B · as of Dec 31, 2025'},
                {'title': 'Also on the list', 'body': 'Fabrikam · $461M carrying value'},
                {'title': "Each figure's basis", 'body': 'Labelled beside it, as of its own date'},
                {'title': 'Public companies', 'body': 'Each one is listed on a U.S. exchange'}],
      'opening_card': {'kicker': 'COMPANY STAKES',
                       'logos': ['BRK-B'],
                       'chip': 'BRK-B',
                       'headline': "Berkshire Hathaway's stakes in public companies",
                       'list': {'rows': [{'logo': 'LTWR',
                                          'chip': 'LTWR',
                                          'cells': ['Litware',
                                                    'As of Dec 31, 2025 · Berkshire Hathaway 2025 shareholder letter',
                                                    '$8.9B'],
                                          'label': 'fair value'},
                                         {'logo': 'FBRK',
                                          'chip': 'FBRK',
                                          'cells': ['Fabrikam',
                                                    'As of Jun 30, 2026 · Berkshire Hathaway 10-Q',
                                                    '$461M'],
                                          'label': 'carrying value'}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'COMPANY STAKES',
                     'header': {'logo': 'BRK-B', 'name': 'Berkshire Hathaway', 'chip': 'BRK-B'},
                     'title': "Berkshire Hathaway's stakes in public companies",
                     'sections': [{'rows': [{'logo': 'LTWR',
                                             'chip': 'LTWR',
                                             'cells': ['Litware',
                                                       'As of Dec 31, 2025 · Berkshire Hathaway 2025 shareholder '
                                                       'letter',
                                                       '$8.9B'],
                                             'label': 'fair value'},
                                            {'logo': 'FBRK',
                                             'chip': 'FBRK',
                                             'cells': ['Fabrikam',
                                                       'As of Jun 30, 2026 · Berkshire Hathaway 10-Q',
                                                       '$461M'],
                                             'label': 'carrying value'}]}],
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': "Berkshire Hathaway's stakes in public companies",
                     'paragraphs': ['The image lists 2 stakes of Berkshire Hathaway in public companies, each with '
                                    'its figure, basis, date and source.',
                                    'Litware, $8.9B fair value; Fabrikam, $461M carrying value.',
                                    'Source: company filings and official releases. As of Dec 31, 2025 to Jun 30, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
      'logo_refs': [{'key': 'BRK-B', 'name': 'Berkshire Hathaway'},
                    {'key': 'LTWR', 'name': 'Litware'},
                    {'key': 'FBRK', 'name': 'Fabrikam'}],
      'source_ref': 'news:company_stakes:3b5d7f91-a2c4-4e6f-8b0d-2f4a6c8e0b10+1f3a5c7e-9b0d-4f2a-8c4e-6a8c0e2a4c09',
      'x': "Berkshire Hathaway's stakes in 2 public companies.\n"
           '\n'
           'Litware: a fair value of $8.9 billion, as of Dec 31, 2025.',
      'threads': "Berkshire Hathaway's stakes in 2 public companies, each with its source and date.\n"
                 '\n'
                 'Litware: a fair value of $8.9 billion, as of Dec 31, 2025.\n'
                 '\n'
                 'Fabrikam: carried at $461 million, as of Jun 30.',
      'facebook': "Berkshire Hathaway's stakes in 2 public companies, each with its source and date.\n"
                  '\n'
                  "Berkshire Hathaway's Litware stake had a fair value of $8.9 billion, as of Dec 31, 2025. Source: "
                  'Berkshire Hathaway 2025 shareholder letter.\n'
                  '\n'
                  "Berkshire Hathaway's Fabrikam stake had a carrying value of $461 million, as of Jun 30. Source: "
                  'Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026).\n'
                  '\n'
                  'Each figure is shown with its own basis and date, from the source named beside it.\n'
                  '\n'
                  'Each of these companies is listed on a U.S. exchange.\n'
                  '\n'
                  'Companies disclose stakes like this in filings and official releases.',
      'tiktok': "Berkshire Hathaway's stakes in 2 public companies, each with its source and date.\n"
                '\n'
                "Berkshire Hathaway's Litware stake had a fair value of $8.9 billion, as of Dec 31, 2025. Source: "
                'Berkshire Hathaway 2025 shareholder letter.',
      'youtube_title': "Berkshire Hathaway's stakes in 2 public companies"},
    'stake_spacex_fair_value': {'hook': "Tesla's SpaceX stake had a fair value of $3 billion.",
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
                       'chip': 'TSLA',
                       'headline': "Tesla's stakes in public companies",
                       'list': {'rows': [{'logo': 'SPCX',
                                          'chip': 'SPCX',
                                          'cells': ['SpaceX', 'As of Jun 30, 2026 · Tesla 10-Q (Q2 2026)', '$3B'],
                                          'label': 'fair value'}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'COMPANY STAKES',
                     'header': {'logo': 'TSLA', 'name': 'Tesla', 'chip': 'TSLA'},
                     'title': "Tesla's stakes in public companies",
                     'sections': [{'rows': [{'logo': 'SPCX',
                                             'chip': 'SPCX',
                                             'cells': ['SpaceX', 'As of Jun 30, 2026 · Tesla 10-Q (Q2 2026)', '$3B'],
                                             'label': 'fair value'}]}],
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': "Tesla's stakes in public companies",
                     'paragraphs': ['The image lists one stake of Tesla in a public company, with its figure, basis, '
                                    'date and source.',
                                    'SpaceX, $3B fair value.',
                                    'Source: company filings and official releases. As of Jun 30, 2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
      'logo_refs': [{'key': 'TSLA', 'name': 'Tesla'}, {'key': 'SPCX', 'name': 'SpaceX'}],
      'source_ref': 'news:company_stakes:7a9e2c44-1b3d-4f5a-8c6e-9d0f1a2b3c03',
      'x': "Tesla's SpaceX stake had a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q (Q2 2026).",
      'threads': "Tesla's SpaceX stake had a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q (Q2 2026).",
      'facebook': "Tesla's SpaceX stake had a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q (Q2 2026).\n"
                  '\n'
                  'Fair value is the amount the source says the stake was measured at on that date.\n'
                  '\n'
                  'Shares of SpaceX are listed on a U.S. exchange.',
      'tiktok': "Tesla's SpaceX stake had a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q (Q2 2026).\n"
                '\n'
                'Fair value is the amount the source says the stake was measured at on that date.',
      'youtube_title': "Tesla's SpaceX stake had a fair value of $3B"},
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
                       'chip': 'NVDA',
                       'headline': "NVIDIA's stakes in public companies",
                       'list': {'rows': [{'logo': 'INTC',
                                          'chip': 'INTC',
                                          'cells': ['Intel', 'As of Dec 26, 2025 · Intel 8-K (Dec 29, 2025)', '$5B'],
                                          'label': 'invested'}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'COMPANY STAKES',
                     'header': {'logo': 'NVDA', 'name': 'NVIDIA', 'chip': 'NVDA'},
                     'title': "NVIDIA's stakes in public companies",
                     'sections': [{'rows': [{'logo': 'INTC',
                                             'chip': 'INTC',
                                             'cells': ['Intel',
                                                       'As of Dec 26, 2025 · Intel 8-K (Dec 29, 2025)',
                                                       '$5B'],
                                             'label': 'invested'}]}],
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': "NVIDIA's stakes in public companies",
                     'paragraphs': ['The image lists one stake of NVIDIA in a public company, with its figure, basis, '
                                    'date and source.',
                                    'Intel, $5B invested.',
                                    'Source: company filings and official releases. As of Dec 26, 2025.']},
      'image_footer': 'Not investment advice · Source: company filings',
      'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'}, {'key': 'INTC', 'name': 'Intel'}],
      'source_ref': 'news:company_stakes:4e6a8c0e-2b4d-4c6e-8a0c-2e4a6c8e0a06',
      'x': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).',
      'threads': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).',
      'facebook': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).\n'
                  '\n'
                  'That is the amount the source reports as invested, not a figure for today.\n'
                  '\n'
                  'Shares of Intel are listed on a U.S. exchange.',
      'tiktok': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).\n'
                '\n'
                'That is the amount the source reports as invested, not a figure for today.',
      'youtube_title': 'NVIDIA invested $5B in Intel'},
    'stake_commitment_public': {'hook': 'Nvidia committed up to $1 billion to Contoso.',
      'video_script': ['The figure is as of June 30th, from the source named in this post.',
                       'That is the most the agreement allows; the source says up to that amount.',
                       'A commitment is money agreed to be invested, which may be paid over time.',
                       'Companies disclose stakes like this in filings and official releases.'],
      'cards': [{'title': 'As of', 'body': 'Jun 30, 2026'},
                {'title': 'Committed up to', 'body': 'The most the agreement allows'},
                {'title': 'A commitment', 'body': 'Money agreed to be invested, which may be paid over time'},
                {'title': 'Source', 'body': 'Contoso 10-Q (quarter ended Jun 30, 2026)'}],
      'opening_card': {'kicker': 'COMPANY STAKES',
                       'logos': ['NVDA'],
                       'chip': 'NVDA',
                       'headline': "NVIDIA's stakes in public companies",
                       'list': {'rows': [{'logo': 'CTSO',
                                          'chip': 'CTSO',
                                          'cells': ['Contoso', 'As of Jun 30, 2026 · Contoso 10-Q', '$1B'],
                                          'label': 'committed up to'}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'COMPANY STAKES',
                     'header': {'logo': 'NVDA', 'name': 'NVIDIA', 'chip': 'NVDA'},
                     'title': "NVIDIA's stakes in public companies",
                     'sections': [{'rows': [{'logo': 'CTSO',
                                             'chip': 'CTSO',
                                             'cells': ['Contoso', 'As of Jun 30, 2026 · Contoso 10-Q', '$1B'],
                                             'label': 'committed up to'}]}],
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': "NVIDIA's stakes in public companies",
                     'paragraphs': ['The image lists one stake of NVIDIA in a public company, with its figure, basis, '
                                    'date and source.',
                                    'Contoso, $1B committed up to.',
                                    'Source: company filings and official releases. As of Jun 30, 2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
      'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'}, {'key': 'CTSO', 'name': 'Contoso'}],
      'source_ref': 'news:company_stakes:9b1d3f5a-7c9e-4b2d-8f0a-1c3e5a7b9d05',
      'x': 'NVIDIA committed up to $1 billion to Contoso, as of Jun 30. Source: Contoso 10-Q (quarter ended Jun 30, '
           '2026).',
      'threads': 'NVIDIA committed up to $1 billion to Contoso, as of Jun 30. Source: Contoso 10-Q (quarter ended Jun '
                 '30, 2026).',
      'facebook': 'NVIDIA committed up to $1 billion to Contoso, as of Jun 30. Source: Contoso 10-Q (quarter ended '
                  'Jun 30, 2026).\n'
                  '\n'
                  'That is the most the agreement allows; the source says up to that amount.\n'
                  '\n'
                  'A commitment is money agreed to be invested, which may be paid over time.',
      'tiktok': 'NVIDIA committed up to $1 billion to Contoso, as of Jun 30. Source: Contoso 10-Q (quarter ended Jun '
                '30, 2026).\n'
                '\n'
                'That is the most the agreement allows; the source says up to that amount.',
      'youtube_title': 'NVIDIA committed up to $1B to Contoso'},
    # news-v21 (main-session decision of 2026-10-20): the record carries a writable revenue pair ($551.9M vs
    # $543.6M) and the post prints none of it — the no-revenue lines, card, picture row and captions of
    # earnings_profit (`T.EARNINGS_REVENUE_IN_POSTS` off: the revenue estimate's basis is unknown)
    'earnings_loss': {'hook': '2 companies reported quarterly results against analyst estimates.',
      'video_script': ['Northwind Fitness: negative $0.05 a share against negative $0.12; revenue $551.9 million '
                       'against $543.6 million.',
                       'Contoso reported $1.12 a share against an estimate of $1.05, and revenue of $3.2 billion '
                       'against $3.3 billion.',
                       'Each estimate is the analyst consensus; a reported figure can be on a different basis from '
                       'the official accounts.',
                       'An analyst estimate is the average figure analysts published before the report.'],
      'cards': [{'title': 'Northwind Fitness',
                 'body': 'EPS −$0.05 vs an estimate of −$0.12 · revenue $551.9M vs $543.6M'},
                {'title': 'Contoso', 'body': 'EPS $1.12 vs an estimate of $1.05 · revenue $3.2B vs $3.3B'},
                {'title': 'About the figures', 'body': 'The consensus basis can differ from the official accounts'},
                {'title': 'The estimate', 'body': 'The average analyst figure published before the report'}],
      'opening_card': {'kicker': 'EARNINGS VS ESTIMATES',
                       'logos': ['NWFT'],
                       'headline': 'Reported vs analyst estimates',
                       'list': {'columns': ['Estimate', 'Reported', 'Surprise'],
                                'rows': [{'logo': 'NWFT',
                                          'chip': 'NWFT',
                                          'cells': ['Northwind Fitness'],
                                          'table': [{'cells': ['EPS', '−$0.12', '−$0.05', '+58.3%']},
                                                    {'cells': ['Revenue', '$543.6M', '$551.9M', '+1.5%']}]},
                                         {'logo': 'CTSO',
                                          'chip': 'CTSO',
                                          'cells': ['Contoso'],
                                          'table': [{'cells': ['EPS', '$1.05', '$1.12', '+6.7%']},
                                                    {'cells': ['Revenue', '$3.3B', '$3.2B', '−3.0%']}]}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'EARNINGS VS ESTIMATES',
                     'title': 'Reported vs analyst estimates',
                     'subtitle': 'Reported Nov 5, 2026',
                     'sections': [{'columns': ['Estimate', 'Reported', 'Surprise'],
                                   'rows': [{'logo': 'NWFT',
                                             'chip': 'NWFT',
                                             'cells': ['Northwind Fitness'],
                                             'table': [{'cells': ['EPS', '−$0.12', '−$0.05', '+58.3%']},
                                                       {'cells': ['Revenue', '$543.6M', '$551.9M', '+1.5%']}]},
                                            {'logo': 'CTSO',
                                             'chip': 'CTSO',
                                             'cells': ['Contoso'],
                                             'table': [{'cells': ['EPS', '$1.05', '$1.12', '+6.7%']},
                                                       {'cells': ['Revenue', '$3.3B', '$3.2B', '−3.0%']}]}]}],
                     'notes': ['The estimate is the analyst consensus; its basis can differ from the official '
                               'accounts.'],
                     'footer': 'Not investment advice · Source: company results and analyst consensus'},
      'image_post': {'title': 'Reported vs analyst estimates',
                     'paragraphs': ['The image compares reported results with analyst estimates for 2 companies: EPS '
                                    'and revenue, each beside its estimate and the gap as a percentage of the '
                                    'estimate.',
                                    'Northwind Fitness, EPS -$0.05 vs an estimate of -$0.12, revenue $551.9M vs '
                                    '$543.6M; Contoso, EPS $1.12 vs an estimate of $1.05, revenue $3.2B vs $3.3B.',
                                    'Source: company results and analyst consensus. Reported Nov 5, 2026.']},
      'image_footer': 'Not investment advice · Source: company results and analyst consensus',
      'persons': [],
      'logo_refs': [{'key': 'NWFT', 'name': 'Northwind Fitness'}, {'key': 'CTSO', 'name': 'Contoso'}],
      'source_ref': 'news:earnings:NWFT:2026-11-05+CTSO:2026-11-05',
      'x': "2 companies reported results against analyst estimates; an estimate's basis can differ from the official "
           'accounts.',
      'bluesky': 'Northwind Fitness and Contoso reported results against analyst estimates.',
      'linkedin': "Northwind Fitness and Contoso reported results against analyst estimates; an estimate's basis can "
                  'differ from the official accounts.\n'
                  '\n'
                  'Northwind Fitness (NWFT): EPS of -$0.05 vs an analyst estimate of -$0.12, and revenue of $551.9 '
                  'million vs an estimate of $543.6 million.\n'
                  '\n'
                  'Contoso (CTSO): EPS of $1.12 vs an analyst estimate of $1.05, and revenue of $3.2 billion vs an '
                  'estimate of $3.3 billion.\n'
                  '\n'
                  'Each company reported these figures on Nov 5.\n'
                  '\n'
                  'An analyst estimate is the average figure analysts published before the report.',
      'tiktok': "Northwind Fitness and Contoso reported results against analyst estimates; an estimate's basis can "
                'differ from the official accounts.\n'
                '\n'
                'Northwind Fitness (NWFT): EPS of -$0.05 vs an analyst estimate of -$0.12, and revenue of $551.9 '
                'million vs an estimate of $543.6 million.',
      'youtube_title': 'Northwind Fitness and Contoso: results vs analyst estimates'},
    'earnings_profit': {'hook': '2 companies reported quarterly results against analyst estimates.',
      'video_script': ['Fabrikam reported $4.86 a share against an estimate of $4.50, and revenue of $2.346 billion '
                       'against $2.346 billion.',
                       'Contoso reported $1.12 a share against an estimate of $1.05, and revenue of $3.2 billion '
                       'against $3.3 billion.',
                       'Each estimate is the analyst consensus; a reported figure can be on a different basis from '
                       'the official accounts.',
                       'An analyst estimate is the average figure analysts published before the report.'],
      'cards': [{'title': 'Fabrikam', 'body': 'EPS $4.86 vs an estimate of $4.50 · revenue $2.346B vs $2.346B'},
                {'title': 'Contoso', 'body': 'EPS $1.12 vs an estimate of $1.05 · revenue $3.2B vs $3.3B'},
                {'title': 'About the figures', 'body': 'The consensus basis can differ from the official accounts'},
                {'title': 'The estimate', 'body': 'The average analyst figure published before the report'}],
      'opening_card': {'kicker': 'EARNINGS VS ESTIMATES',
                       'logos': ['FBKM'],
                       'headline': 'Reported vs analyst estimates',
                       'list': {'columns': ['Estimate', 'Reported', 'Surprise'],
                                'rows': [{'logo': 'FBKM',
                                          'chip': 'FBKM',
                                          'cells': ['Fabrikam'],
                                          'table': [{'cells': ['EPS', '$4.50', '$4.86', '+8.0%']},
                                                    {'cells': ['Revenue', '$2.346B', '$2.346B', '0.0%']}]},
                                         {'logo': 'CTSO',
                                          'chip': 'CTSO',
                                          'cells': ['Contoso'],
                                          'table': [{'cells': ['EPS', '$1.05', '$1.12', '+6.7%']},
                                                    {'cells': ['Revenue', '$3.3B', '$3.2B', '−3.0%']}]}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': 'EARNINGS VS ESTIMATES',
                     'title': 'Reported vs analyst estimates',
                     'subtitle': 'Reported Nov 10, 2026',
                     'sections': [{'columns': ['Estimate', 'Reported', 'Surprise'],
                                   'rows': [{'logo': 'FBKM',
                                             'chip': 'FBKM',
                                             'cells': ['Fabrikam'],
                                             'table': [{'cells': ['EPS', '$4.50', '$4.86', '+8.0%']},
                                                       {'cells': ['Revenue', '$2.346B', '$2.346B', '0.0%']}]},
                                            {'logo': 'CTSO',
                                             'chip': 'CTSO',
                                             'cells': ['Contoso'],
                                             'table': [{'cells': ['EPS', '$1.05', '$1.12', '+6.7%']},
                                                       {'cells': ['Revenue', '$3.3B', '$3.2B', '−3.0%']}]}]}],
                     'notes': ['The estimate is the analyst consensus; its basis can differ from the official '
                               'accounts.'],
                     'footer': 'Not investment advice · Source: company results and analyst consensus'},
      'image_post': {'title': 'Reported vs analyst estimates',
                     'paragraphs': ['The image compares reported results with analyst estimates for 2 companies: EPS '
                                    'and revenue, each beside its estimate and the gap as a percentage of the '
                                    'estimate.',
                                    'Fabrikam, EPS $4.86 vs an estimate of $4.50, revenue $2.346B vs $2.346B; '
                                    'Contoso, EPS $1.12 vs an estimate of $1.05, revenue $3.2B vs $3.3B.',
                                    'Source: company results and analyst consensus. Reported Nov 10, 2026.']},
      'image_footer': 'Not investment advice · Source: company results and analyst consensus',
      'persons': [],
      'logo_refs': [{'key': 'FBKM', 'name': 'Fabrikam'}, {'key': 'CTSO', 'name': 'Contoso'}],
      'source_ref': 'news:earnings:FBKM:2026-11-10+CTSO:2026-11-10',
      'x': "Fabrikam and Contoso reported results against analyst estimates; an estimate's basis can differ from the "
           'official accounts.',
      'bluesky': 'Fabrikam and Contoso reported results against analyst estimates.',
      'linkedin': "Fabrikam and Contoso reported results against analyst estimates; an estimate's basis can differ "
                  'from the official accounts.\n'
                  '\n'
                  'Fabrikam (FBKM): EPS of $4.86 vs an analyst estimate of $4.50, and revenue of $2.346 billion vs an '
                  'estimate of $2.346 billion.\n'
                  '\n'
                  'Contoso (CTSO): EPS of $1.12 vs an analyst estimate of $1.05, and revenue of $3.2 billion vs an '
                  'estimate of $3.3 billion.\n'
                  '\n'
                  'Each company reported these figures on Nov 10.\n'
                  '\n'
                  'An analyst estimate is the average figure analysts published before the report.',
      'tiktok': "Fabrikam and Contoso reported results against analyst estimates; an estimate's basis can differ from "
                'the official accounts.\n'
                '\n'
                'Fabrikam (FBKM): EPS of $4.86 vs an analyst estimate of $4.50, and revenue of $2.346 billion vs an '
                'estimate of $2.346 billion.',
      'youtube_title': 'Fabrikam and Contoso: results vs analyst estimates'},
    # news-v19 (the review of 2026-10-16, TH-1): the headlines say "by largest revenue line" — "product or business
    # line" was false for a member whose breakdown is regional
    'theme_ai_chips': {'hook': 'Where do 14 companies in AI chips get their revenue?',
      'video_script': ["Nvidia's largest revenue line in fiscal 2026 was Data Center.",
                       "Broadcom's largest revenue line in fiscal 2025 was Semiconductor Solutions, and AMD's was "
                       'Data Center.',
                       "Marvell Technology's largest revenue line in fiscal 2026 was Data Center.",
                       "This grouping is Caydex's own; its members can change."],
      'cards': [{'title': 'NVIDIA', 'body': 'Largest revenue line in fiscal 2026: Data Center'},
                {'title': 'Broadcom and AMD', 'body': 'Fiscal 2025: Semiconductor Solutions · Data Center'},
                {'title': 'Marvell Technology', 'body': 'Largest revenue line in fiscal 2026: Data Center'},
                {'title': 'A Caydex grouping', 'body': 'Its members can change'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['NVDA'],
                       'figure': '14',
                       'headline': 'AI chips',
                       'list': {'heading': "14 companies · each company's largest business and its share of total "
                                           'revenue · fiscal 2025 or 2026',
                                'rows': [{'logo': 'NVDA',
                                          'chip': 'NVDA',
                                          'cells': ['NVIDIA', 'Data Center · 88% of revenue']},
                                         {'logo': 'AVGO',
                                          'chip': 'AVGO',
                                          'cells': ['Broadcom', 'Semiconductor Solutions · 58% of revenue']},
                                         {'logo': 'AMD',
                                          'chip': 'AMD',
                                          'cells': ['AMD', 'Data Center · 48% of revenue']},
                                         {'logo': 'MRVL',
                                          'chip': 'MRVL',
                                          'cells': ['Marvell Technology', 'Data Center · 74% of revenue']}],
                                'more': '+10 more'}},
      'image_spec': {'layout': 'grid',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'AI chips',
                     'subtitle': "14 companies · each company's largest business and its share of total revenue · "
                                 'fiscal 2025 or 2026',
                     'sub_break': 7,
                     'tiles': [{'logo': 'NVDA',
                                'chip': 'NVDA',
                                'name': 'NVIDIA',
                                'line': 'Data Center · 88% of revenue'},
                               {'logo': 'AVGO',
                                'chip': 'AVGO',
                                'name': 'Broadcom',
                                'line': 'Semiconductor Solutions · 58% of revenue'},
                               {'logo': 'AMD', 'chip': 'AMD', 'name': 'AMD', 'line': 'Data Center · 48% of revenue'},
                               {'logo': 'MRVL',
                                'chip': 'MRVL',
                                'name': 'Marvell Technology',
                                'line': 'Data Center · 74% of revenue'},
                               {'logo': 'QCOM', 'chip': 'QCOM', 'name': 'Qualcomm', 'line': 'QCT · 84% of revenue'},
                               {'logo': 'ARM', 'chip': 'ARM', 'name': 'Arm Holdings', 'line': 'Royalty'},
                               {'logo': 'MU',
                                'chip': 'MU',
                                'name': 'Micron Technology',
                                'line': 'Compute and Networking · 43% of revenue'},
                               {'logo': 'INTC',
                                'chip': 'INTC',
                                'name': 'Intel',
                                'line': 'Client Computing, FY2022 · 55% of revenue'},
                               {'logo': 'TXN',
                                'chip': 'TXN',
                                'name': 'Texas Instruments',
                                'line': 'Analog · 77% of revenue'},
                               {'logo': 'ADI',
                                'chip': 'ADI',
                                'name': 'Analog Devices',
                                'line': 'Industrial · 45% of revenue'},
                               {'logo': 'SNPS',
                                'chip': 'SNPS',
                                'name': 'Synopsys',
                                'line': 'EDA Software · 81% of revenue'},
                               {'logo': 'CDNS',
                                'chip': 'CDNS',
                                'name': 'Cadence Design Systems',
                                'line': 'Core EDA · 70% of revenue'}],
                     'icon': 'chip',
                     'more': '+2 more',
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': 'AI chips',
                     'paragraphs': ['The image shows 12 of the 14 companies in AI chips. It gives the largest revenue '
                                    'line and its share of revenue in fiscal 2025 or 2026 for 11 of them. A line from '
                                    'an earlier fiscal year is marked with its year.',
                                    'Shown: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; Micron '
                                    'Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design '
                                    'Systems.',
                                    'Source: company revenue breakdowns; grouping by Caydex. Members as of Nov 1, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
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
      'x': 'AI chips: where 14 companies get their revenue.\n\nLam Research: Systems, 66% of FY2025 revenue\n\nKLA',
      'bluesky': 'AI chips: where 14 companies get their revenue.\n'
                 '\n'
                 'Lam Research: Systems, 66% of FY2025 revenue\n'
                 '\n'
                 'KLA',
      'linkedin': 'AI chips: where 14 companies get their revenue, by largest revenue line.\n'
                  '\n'
                  'NVIDIA: Data Center, 88% of fiscal 2026 revenue.\n'
                  '\n'
                  'Broadcom: Semiconductor Solutions, 58% of fiscal 2025 revenue.\n'
                  '\n'
                  'AMD: Data Center, 48% of fiscal 2025 revenue.\n'
                  '\n'
                  'Marvell Technology: Data Center, 74% of fiscal 2026 revenue.\n'
                  '\n'
                  'Qualcomm: QCT, 84% of fiscal 2025 revenue.\n'
                  '\n'
                  'Arm Holdings: Royalty, its largest revenue line in fiscal 2026.\n'
                  '\n'
                  'Micron Technology: Compute and Networking, 43% of fiscal 2025 revenue.\n'
                  '\n'
                  'Intel: Client Computing, 55% of fiscal 2022 revenue.\n'
                  '\n'
                  'Texas Instruments: Analog, 77% of fiscal 2025 revenue.\n'
                  '\n'
                  'Analog Devices: Industrial, 45% of fiscal 2025 revenue.\n'
                  '\n'
                  'Synopsys: Electronic Design Automation Software, 81% of fiscal 2025 revenue.\n'
                  '\n'
                  'Cadence Design Systems: Core EDA, 70% of fiscal 2025 revenue.\n'
                  '\n'
                  'Lam Research: Systems, 66% of fiscal 2025 revenue.\n'
                  '\n'
                  'KLA is also in AI chips.\n'
                  '\n'
                  "A revenue line is one product, business or region in a company's revenue breakdown.\n"
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'AI chips: where 14 companies get their revenue, by largest revenue line.\n'
                '\n'
                'Qualcomm: QCT, 84% of FY2025 revenue\n'
                '\n'
                'Arm Holdings: Royalty, FY2026\n'
                '\n'
                'Micron Technology: Compute and Networking, 43% of FY2025 revenue\n'
                '\n'
                'Intel: Client Computing, 55% of FY2022 revenue\n'
                '\n'
                'Texas Instruments: Analog, 77% of FY2025 revenue\n'
                '\n'
                'Analog Devices: Industrial, 45% of FY2025 revenue\n'
                '\n'
                'Synopsys: EDA Software, 81% of FY2025 revenue\n'
                '\n'
                'Cadence Design Systems: Core EDA, 70% of FY2025 revenue\n'
                '\n'
                'Lam Research: Systems, 66% of FY2025 revenue\n'
                '\n'
                'KLA',
      'youtube_title': 'AI chips: where 14 companies get their revenue'},
    'theme_ai_chips_of_more': {'hook': 'Where do 14 of the 16 companies in AI chips get their revenue?',
      'video_script': ["Nvidia's largest revenue line in fiscal 2026 was Data Center.",
                       "Broadcom's largest revenue line in fiscal 2025 was Semiconductor Solutions, and AMD's was "
                       'Data Center.',
                       "Marvell Technology's largest revenue line in fiscal 2026 was Data Center.",
                       "This grouping is Caydex's own; its members can change."],
      'cards': [{'title': 'NVIDIA', 'body': 'Largest revenue line in fiscal 2026: Data Center'},
                {'title': 'Broadcom and AMD', 'body': 'Fiscal 2025: Semiconductor Solutions · Data Center'},
                {'title': 'Marvell Technology', 'body': 'Largest revenue line in fiscal 2026: Data Center'},
                {'title': 'A Caydex grouping', 'body': 'Its members can change'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['NVDA'],
                       'figure': '14',
                       'headline': 'AI chips',
                       'list': {'heading': "14 of its 16 companies · each company's largest business and its share of "
                                           'total revenue · fiscal 2025 or 2026',
                                'rows': [{'logo': 'NVDA',
                                          'chip': 'NVDA',
                                          'cells': ['NVIDIA', 'Data Center · 88% of revenue']},
                                         {'logo': 'AVGO',
                                          'chip': 'AVGO',
                                          'cells': ['Broadcom', 'Semiconductor Solutions · 58% of revenue']},
                                         {'logo': 'AMD',
                                          'chip': 'AMD',
                                          'cells': ['AMD', 'Data Center · 48% of revenue']},
                                         {'logo': 'MRVL',
                                          'chip': 'MRVL',
                                          'cells': ['Marvell Technology', 'Data Center · 74% of revenue']}],
                                'more': '+10 more'}},
      'image_spec': {'layout': 'grid',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'AI chips',
                     'subtitle': "14 of its 16 companies · each company's largest business and its share of total "
                                 'revenue · fiscal 2025 or 2026',
                     'sub_break': 10,
                     'tiles': [{'logo': 'NVDA',
                                'chip': 'NVDA',
                                'name': 'NVIDIA',
                                'line': 'Data Center · 88% of revenue'},
                               {'logo': 'AVGO',
                                'chip': 'AVGO',
                                'name': 'Broadcom',
                                'line': 'Semiconductor Solutions · 58% of revenue'},
                               {'logo': 'AMD', 'chip': 'AMD', 'name': 'AMD', 'line': 'Data Center · 48% of revenue'},
                               {'logo': 'MRVL',
                                'chip': 'MRVL',
                                'name': 'Marvell Technology',
                                'line': 'Data Center · 74% of revenue'},
                               {'logo': 'QCOM', 'chip': 'QCOM', 'name': 'Qualcomm', 'line': 'QCT · 84% of revenue'},
                               {'logo': 'ARM', 'chip': 'ARM', 'name': 'Arm Holdings', 'line': 'Royalty'},
                               {'logo': 'MU',
                                'chip': 'MU',
                                'name': 'Micron Technology',
                                'line': 'Compute and Networking · 43% of revenue'},
                               {'logo': 'INTC',
                                'chip': 'INTC',
                                'name': 'Intel',
                                'line': 'Client Computing, FY2022 · 55% of revenue'},
                               {'logo': 'TXN',
                                'chip': 'TXN',
                                'name': 'Texas Instruments',
                                'line': 'Analog · 77% of revenue'},
                               {'logo': 'ADI',
                                'chip': 'ADI',
                                'name': 'Analog Devices',
                                'line': 'Industrial · 45% of revenue'},
                               {'logo': 'SNPS',
                                'chip': 'SNPS',
                                'name': 'Synopsys',
                                'line': 'EDA Software · 81% of revenue'},
                               {'logo': 'CDNS',
                                'chip': 'CDNS',
                                'name': 'Cadence Design Systems',
                                'line': 'Core EDA · 70% of revenue'}],
                     'icon': 'chip',
                     'more': '+2 more',
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': 'AI chips',
                     'paragraphs': ['The image shows 12 of the 16 companies in AI chips. It gives the largest revenue '
                                    'line and its share of revenue in fiscal 2025 or 2026 for 11 of them. A line from '
                                    'an earlier fiscal year is marked with its year.',
                                    'Shown: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; Micron '
                                    'Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design '
                                    'Systems.',
                                    'Source: company revenue breakdowns; grouping by Caydex. Members as of Nov 1, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
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
      'x': 'AI chips: where 14 of its 16 companies get their revenue.\n'
           '\n'
           'Lam Research: Systems, 66% of FY2025 revenue\n'
           '\n'
           'KLA',
      'bluesky': 'AI chips: where 14 of its 16 companies get their revenue.\n'
                 '\n'
                 'Lam Research: Systems, 66% of FY2025 revenue\n'
                 '\n'
                 'KLA',
      'linkedin': 'AI chips: where 14 of its 16 companies get their revenue, by largest revenue line.\n'
                  '\n'
                  'NVIDIA: Data Center, 88% of fiscal 2026 revenue.\n'
                  '\n'
                  'Broadcom: Semiconductor Solutions, 58% of fiscal 2025 revenue.\n'
                  '\n'
                  'AMD: Data Center, 48% of fiscal 2025 revenue.\n'
                  '\n'
                  'Marvell Technology: Data Center, 74% of fiscal 2026 revenue.\n'
                  '\n'
                  'Qualcomm: QCT, 84% of fiscal 2025 revenue.\n'
                  '\n'
                  'Arm Holdings: Royalty, its largest revenue line in fiscal 2026.\n'
                  '\n'
                  'Micron Technology: Compute and Networking, 43% of fiscal 2025 revenue.\n'
                  '\n'
                  'Intel: Client Computing, 55% of fiscal 2022 revenue.\n'
                  '\n'
                  'Texas Instruments: Analog, 77% of fiscal 2025 revenue.\n'
                  '\n'
                  'Analog Devices: Industrial, 45% of fiscal 2025 revenue.\n'
                  '\n'
                  'Synopsys: Electronic Design Automation Software, 81% of fiscal 2025 revenue.\n'
                  '\n'
                  'Cadence Design Systems: Core EDA, 70% of fiscal 2025 revenue.\n'
                  '\n'
                  'Lam Research: Systems, 66% of fiscal 2025 revenue.\n'
                  '\n'
                  'KLA is also in AI chips.\n'
                  '\n'
                  "A revenue line is one product, business or region in a company's revenue breakdown.\n"
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'AI chips: where 14 of its 16 companies get their revenue, by largest revenue line.\n'
                '\n'
                'Qualcomm: QCT, 84% of FY2025 revenue\n'
                '\n'
                'Arm Holdings: Royalty, FY2026\n'
                '\n'
                'Micron Technology: Compute and Networking, 43% of FY2025 revenue\n'
                '\n'
                'Intel: Client Computing, 55% of FY2022 revenue\n'
                '\n'
                'Texas Instruments: Analog, 77% of FY2025 revenue\n'
                '\n'
                'Analog Devices: Industrial, 45% of FY2025 revenue\n'
                '\n'
                'Synopsys: EDA Software, 81% of FY2025 revenue\n'
                '\n'
                'Cadence Design Systems: Core EDA, 70% of FY2025 revenue\n'
                '\n'
                'Lam Research: Systems, 66% of FY2025 revenue\n'
                '\n'
                'KLA',
      'youtube_title': 'AI chips: where 14 of its 16 companies get their revenue'},
    'theme_ai_chips_weekly': {'hook': 'Where do 14 of the 16 companies in AI chips get their revenue?',
      'video_script': ["Nvidia's largest revenue line in fiscal 2026 was Data Center.",
                       "Broadcom's largest revenue line in fiscal 2025 was Semiconductor Solutions, and AMD's was "
                       'Data Center.',
                       "Marvell Technology's largest revenue line in fiscal 2026 was Data Center.",
                       "This grouping is Caydex's own; its members can change."],
      'cards': [{'title': 'NVIDIA', 'body': 'Largest revenue line in fiscal 2026: Data Center'},
                {'title': 'Broadcom and AMD', 'body': 'Fiscal 2025: Semiconductor Solutions · Data Center'},
                {'title': 'Marvell Technology', 'body': 'Largest revenue line in fiscal 2026: Data Center'},
                {'title': 'A Caydex grouping', 'body': 'Its members can change'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['NVDA'],
                       'figure': '14',
                       'headline': 'AI chips',
                       'list': {'heading': "14 of its 16 companies · each company's largest business and its share of "
                                           'total revenue · fiscal 2025 or 2026',
                                'rows': [{'logo': 'NVDA',
                                          'chip': 'NVDA',
                                          'cells': ['NVIDIA', 'Data Center · 88% of revenue']},
                                         {'logo': 'AVGO',
                                          'chip': 'AVGO',
                                          'cells': ['Broadcom', 'Semiconductor Solutions · 58% of revenue']},
                                         {'logo': 'AMD',
                                          'chip': 'AMD',
                                          'cells': ['AMD', 'Data Center · 48% of revenue']},
                                         {'logo': 'MRVL',
                                          'chip': 'MRVL',
                                          'cells': ['Marvell Technology', 'Data Center · 74% of revenue']}],
                                'more': '+10 more'}},
      'image_spec': {'layout': 'grid',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'AI chips',
                     'subtitle': "14 of its 16 companies · each company's largest business and its share of total "
                                 'revenue · fiscal 2025 or 2026',
                     'sub_break': 10,
                     'tiles': [{'logo': 'NVDA',
                                'chip': 'NVDA',
                                'name': 'NVIDIA',
                                'line': 'Data Center · 88% of revenue'},
                               {'logo': 'AVGO',
                                'chip': 'AVGO',
                                'name': 'Broadcom',
                                'line': 'Semiconductor Solutions · 58% of revenue'},
                               {'logo': 'AMD', 'chip': 'AMD', 'name': 'AMD', 'line': 'Data Center · 48% of revenue'},
                               {'logo': 'MRVL',
                                'chip': 'MRVL',
                                'name': 'Marvell Technology',
                                'line': 'Data Center · 74% of revenue'},
                               {'logo': 'QCOM', 'chip': 'QCOM', 'name': 'Qualcomm', 'line': 'QCT · 84% of revenue'},
                               {'logo': 'ARM', 'chip': 'ARM', 'name': 'Arm Holdings', 'line': 'Royalty'},
                               {'logo': 'MU',
                                'chip': 'MU',
                                'name': 'Micron Technology',
                                'line': 'Compute and Networking · 43% of revenue'},
                               {'logo': 'INTC',
                                'chip': 'INTC',
                                'name': 'Intel',
                                'line': 'Client Computing, FY2022 · 55% of revenue'},
                               {'logo': 'TXN',
                                'chip': 'TXN',
                                'name': 'Texas Instruments',
                                'line': 'Analog · 77% of revenue'},
                               {'logo': 'ADI',
                                'chip': 'ADI',
                                'name': 'Analog Devices',
                                'line': 'Industrial · 45% of revenue'},
                               {'logo': 'SNPS',
                                'chip': 'SNPS',
                                'name': 'Synopsys',
                                'line': 'EDA Software · 81% of revenue'},
                               {'logo': 'CDNS',
                                'chip': 'CDNS',
                                'name': 'Cadence Design Systems',
                                'line': 'Core EDA · 70% of revenue'}],
                     'icon': 'chip',
                     'more': '+2 more',
                     'footer': 'Not investment advice · Source: company filings'},
      'image_post': {'title': 'AI chips',
                     'paragraphs': ['The image shows 12 of the 16 companies in AI chips. It gives the largest revenue '
                                    'line and its share of revenue in fiscal 2025 or 2026 for 11 of them. A line from '
                                    'an earlier fiscal year is marked with its year.',
                                    'Shown: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; Micron '
                                    'Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design '
                                    'Systems.',
                                    'Source: company revenue breakdowns; grouping by Caydex. Members as of Nov 18, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company filings',
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
      'source_ref': 'news:theme_explainer:ai-chips:make_money:2026-11-25',
      'x': 'AI chips: where 14 of its 16 companies get their revenue.\n'
           '\n'
           'Lam Research: Systems, 66% of FY2025 revenue\n'
           '\n'
           'KLA',
      'bluesky': 'AI chips: where 14 of its 16 companies get their revenue.\n'
                 '\n'
                 'Lam Research: Systems, 66% of FY2025 revenue\n'
                 '\n'
                 'KLA',
      'linkedin': 'AI chips: where 14 of its 16 companies get their revenue, by largest revenue line.\n'
                  '\n'
                  'NVIDIA: Data Center, 88% of fiscal 2026 revenue.\n'
                  '\n'
                  'Broadcom: Semiconductor Solutions, 58% of fiscal 2025 revenue.\n'
                  '\n'
                  'AMD: Data Center, 48% of fiscal 2025 revenue.\n'
                  '\n'
                  'Marvell Technology: Data Center, 74% of fiscal 2026 revenue.\n'
                  '\n'
                  'Qualcomm: QCT, 84% of fiscal 2025 revenue.\n'
                  '\n'
                  'Arm Holdings: Royalty, its largest revenue line in fiscal 2026.\n'
                  '\n'
                  'Micron Technology: Compute and Networking, 43% of fiscal 2025 revenue.\n'
                  '\n'
                  'Intel: Client Computing, 55% of fiscal 2022 revenue.\n'
                  '\n'
                  'Texas Instruments: Analog, 77% of fiscal 2025 revenue.\n'
                  '\n'
                  'Analog Devices: Industrial, 45% of fiscal 2025 revenue.\n'
                  '\n'
                  'Synopsys: Electronic Design Automation Software, 81% of fiscal 2025 revenue.\n'
                  '\n'
                  'Cadence Design Systems: Core EDA, 70% of fiscal 2025 revenue.\n'
                  '\n'
                  'Lam Research: Systems, 66% of fiscal 2025 revenue.\n'
                  '\n'
                  'KLA is also in AI chips.\n'
                  '\n'
                  "A revenue line is one product, business or region in a company's revenue breakdown.\n"
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'AI chips: where 14 of its 16 companies get their revenue, by largest revenue line.\n'
                '\n'
                'Qualcomm: QCT, 84% of FY2025 revenue\n'
                '\n'
                'Arm Holdings: Royalty, FY2026\n'
                '\n'
                'Micron Technology: Compute and Networking, 43% of FY2025 revenue\n'
                '\n'
                'Intel: Client Computing, 55% of FY2022 revenue\n'
                '\n'
                'Texas Instruments: Analog, 77% of FY2025 revenue\n'
                '\n'
                'Analog Devices: Industrial, 45% of FY2025 revenue\n'
                '\n'
                'Synopsys: EDA Software, 81% of FY2025 revenue\n'
                '\n'
                'Cadence Design Systems: Core EDA, 70% of FY2025 revenue\n'
                '\n'
                'Lam Research: Systems, 66% of FY2025 revenue\n'
                '\n'
                'KLA',
      'youtube_title': 'AI chips: where 14 of its 16 companies get their revenue'},
    'theme_growth_silicon': {'hook': 'Silicon Rush: revenue growth and profit at 12 of its 21 companies.',
      'video_script': ['At Nvidia, fiscal 2026 revenue was 114% higher than the year before, with $56 of profit per '
                       '$100 of revenue.',
                       'At TSMC, fiscal 2025 revenue was 34% higher than the year before, with $40 of profit per $100 '
                       'of revenue.',
                       'Profit is net income; growth compares revenue with the year before.',
                       "This grouping is Caydex's own; its members can change."],
      'cards': [{'title': 'NVIDIA',
                 'body': 'Fiscal 2026 revenue 114% higher than the year before; $56 of profit per $100 of revenue'},
                {'title': 'TSMC',
                 'body': 'Fiscal 2025 revenue 34% higher than the year before; $40 of profit per $100 of revenue'},
                {'title': 'What the figures are',
                 'body': 'Profit is net income per $100 of revenue; growth compares revenue with the year before'},
                {'title': 'A Caydex grouping', 'body': 'Its members can change'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['NVDA'],
                       'figure': '12',
                       'headline': 'Silicon Rush',
                       'list': {'layout': 'figures',
                                'heading': '12 of its 21 companies · revenue growth and profit · latest fiscal year',
                                'rows': [{'logo': 'NVDA',
                                          'chip': 'NVDA',
                                          'name': 'NVIDIA',
                                          'growth': '+114%',
                                          'ratio': 1.0,
                                          'profit': '$56'},
                                         {'logo': 'TSM',
                                          'chip': 'TSM',
                                          'name': 'TSMC',
                                          'growth': '+34%',
                                          'ratio': 0.2982,
                                          'profit': '$40'},
                                         {'logo': 'AVGO',
                                          'chip': 'AVGO',
                                          'name': 'Broadcom',
                                          'growth': '+44%',
                                          'ratio': 0.386,
                                          'profit': '$22'},
                                         {'logo': 'ASML',
                                          'chip': 'ASML',
                                          'name': 'ASML',
                                          'growth': '+2%',
                                          'ratio': 0.0175,
                                          'profit': '$27'},
                                         {'logo': 'AMD',
                                          'chip': 'AMD',
                                          'name': 'AMD',
                                          'growth': '+14%',
                                          'ratio': 0.1228,
                                          'profit': '$6'},
                                         {'logo': 'MU',
                                          'chip': 'MU',
                                          'name': 'Micron Technology',
                                          'growth': '+49%',
                                          'ratio': 0.4298,
                                          'profit': '$11'}],
                                'heads': [['Revenue growth'], ['Profit per $100', 'of revenue']],
                                'more': '+6 more',
                                'note': 'Profit = net income; growth = revenue vs the year before'}},
      'image_spec': {'layout': 'figures',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'Silicon Rush',
                     'subtitle': '12 of its 21 companies · revenue growth and profit · latest fiscal year',
                     'heads': [['Revenue growth'], ['Profit per $100', 'of revenue']],
                     'rows': [{'logo': 'NVDA',
                               'chip': 'NVDA',
                               'name': 'NVIDIA',
                               'growth': '+114%',
                               'ratio': 1.0,
                               'profit': '$56'},
                              {'logo': 'TSM',
                               'chip': 'TSM',
                               'name': 'TSMC',
                               'growth': '+34%',
                               'ratio': 0.2982,
                               'profit': '$40'},
                              {'logo': 'AVGO',
                               'chip': 'AVGO',
                               'name': 'Broadcom',
                               'growth': '+44%',
                               'ratio': 0.386,
                               'profit': '$22'},
                              {'logo': 'ASML',
                               'chip': 'ASML',
                               'name': 'ASML',
                               'growth': '+2%',
                               'ratio': 0.0175,
                               'profit': '$27'},
                              {'logo': 'AMD',
                               'chip': 'AMD',
                               'name': 'AMD',
                               'growth': '+14%',
                               'ratio': 0.1228,
                               'profit': '$6'},
                              {'logo': 'MU',
                               'chip': 'MU',
                               'name': 'Micron Technology',
                               'growth': '+49%',
                               'ratio': 0.4298,
                               'profit': '$11'},
                              {'logo': 'ARM',
                               'chip': 'ARM',
                               'name': 'Arm Holdings',
                               'growth': '+24%',
                               'ratio': 0.2105,
                               'profit': '$19'},
                              {'logo': 'MRVL',
                               'chip': 'MRVL',
                               'name': 'Marvell Technology',
                               'growth': '+5%',
                               'ratio': 0.0439,
                               'profit': '−$17'},
                              {'logo': 'QCOM',
                               'chip': 'QCOM',
                               'name': 'Qualcomm',
                               'growth': '+9%',
                               'ratio': 0.0789,
                               'profit': '$26'},
                              {'logo': 'AMAT',
                               'chip': 'AMAT',
                               'name': 'Applied Materials',
                               'growth': '+2%',
                               'ratio': 0.0175,
                               'profit': '$26'}],
                     'note': 'Profit = net income; growth = revenue vs the year before',
                     'icon': 'wafer',
                     'more': '+2 more',
                     'footer': 'Not investment advice · Source: company financial statements · fiscal 2025 or 2026'},
      'image_post': {'title': 'Silicon Rush',
                     'paragraphs': ['The image lists 10 of the 21 companies in Silicon Rush, each with its revenue '
                                    'growth over the year before and its net income per $100 of revenue, in its '
                                    'latest fiscal year.',
                                    'Shown: NVIDIA; TSMC; Broadcom; ASML; AMD; Micron Technology; Arm Holdings; '
                                    'Marvell Technology; Qualcomm; Applied Materials.',
                                    'Source: company financial statements; grouping by Caydex. Members as of Nov 18, '
                                    '2026.']},
      'image_footer': 'Not investment advice · Source: company financial statements · fiscal 2025 or 2026',
      'persons': [],
      'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'},
                    {'key': 'TSM', 'name': 'TSMC'},
                    {'key': 'AVGO', 'name': 'Broadcom'},
                    {'key': 'ASML', 'name': 'ASML'},
                    {'key': 'AMD', 'name': 'AMD'},
                    {'key': 'MU', 'name': 'Micron Technology'},
                    {'key': 'ARM', 'name': 'Arm Holdings'},
                    {'key': 'MRVL', 'name': 'Marvell Technology'},
                    {'key': 'QCOM', 'name': 'Qualcomm'},
                    {'key': 'AMAT', 'name': 'Applied Materials'}],
      'source_ref': 'news:theme_explainer:silicon-rush:growth_profit:2026-11-25',
      'x': 'Silicon Rush: revenue growth and profit at 12 of its 21 companies.\n'
           '\n'
           'NVIDIA: revenue +114%, $56 profit per $100',
      'bluesky': 'Silicon Rush: revenue growth and profit at 12 of its 21 companies, each in its latest fiscal year.',
      'linkedin': 'Silicon Rush: revenue growth and profit at 12 of its 21 companies, each in its latest fiscal '
                  'year.\n'
                  '\n'
                  'NVIDIA: revenue +114%, $56 profit per $100\n'
                  '\n'
                  'TSMC: revenue +34%, $40 profit per $100\n'
                  '\n'
                  'Broadcom: revenue +44%, $22 profit per $100\n'
                  '\n'
                  'ASML: revenue +2%, $27 profit per $100\n'
                  '\n'
                  'AMD: revenue +14%, $6 profit per $100\n'
                  '\n'
                  'Micron Technology: revenue +49%, $11 profit per $100\n'
                  '\n'
                  'Arm Holdings: revenue +24%, $19 profit per $100\n'
                  '\n'
                  'Marvell Technology: revenue +5%, $17 loss per $100\n'
                  '\n'
                  'Qualcomm: revenue +9%, $26 profit per $100\n'
                  '\n'
                  'Applied Materials: revenue +2%, $26 profit per $100\n'
                  '\n'
                  'Intel: revenue −2%, $19 loss per $100\n'
                  '\n'
                  'Texas Instruments: revenue +6%, $28 profit per $100\n'
                  '\n'
                  "Growth compares each company's latest fiscal year of revenue with the year before; profit is its "
                  'net income for every $100 of that revenue.\n'
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'Silicon Rush: revenue growth and profit at 12 of its 21 companies, each in its latest fiscal year.\n'
                '\n'
                'Arm Holdings: revenue +24%, $19 profit per $100\n'
                '\n'
                'Marvell Technology: revenue +5%, $17 loss per $100\n'
                '\n'
                'Qualcomm: revenue +9%, $26 profit per $100\n'
                '\n'
                'Applied Materials: revenue +2%, $26 profit per $100\n'
                '\n'
                'Intel: revenue −2%, $19 loss per $100\n'
                '\n'
                'Texas Instruments: revenue +6%, $28 profit per $100',
      'youtube_title': 'Silicon Rush: revenue growth and profit at 12 of its 21 companies'},
    'theme_insiders_cyber': {'hook': 'Cyber Wars: insider purchases and sales disclosed in the last 90 days.',
      'video_script': ['Insiders at 3 of the 15 companies checked disclosed purchases of their stock, the most at '
                       'Rapid7, about $2.1 million.',
                       'Insiders at 9 of the 15 companies disclosed sales of their stock, the most at CrowdStrike, '
                       'about $186 million.',
                       'Only purchases and sales count here, and sales can be pre-planned under Rule 10b5-1.',
                       "This grouping is Caydex's own; its members can change."],
      'cards': [{'title': 'Disclosed purchases', 'body': 'Rapid7: $2.1M · SentinelOne: $1.2M'},
                {'title': 'Disclosed sales', 'body': 'CrowdStrike: $186M · Cloudflare: $128M'},
                {'title': 'Purchases and sales only', 'body': 'Sales can be pre-planned under Rule 10b5-1'},
                {'title': 'A Caydex grouping', 'body': 'Its members can change'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['RPD'],
                       'figure': '11',
                       'headline': 'Cyber Wars',
                       'list': {'layout': 'ledger',
                                'heading': '15 companies · insider filings · last 90 days',
                                'rows': [{'logo': 'RPD',
                                          'chip': 'RPD',
                                          'name': 'Rapid7',
                                          'buys': '$2.1M',
                                          'sells': '—'},
                                         {'logo': 'S',
                                          'chip': 'S',
                                          'name': 'SentinelOne',
                                          'buys': '$1.2M',
                                          'sells': '$8.6M'},
                                         {'logo': 'TENB',
                                          'chip': 'TENB',
                                          'name': 'Tenable',
                                          'buys': '$640K',
                                          'sells': '—'},
                                         {'logo': 'CRWD',
                                          'chip': 'CRWD',
                                          'name': 'CrowdStrike',
                                          'buys': '—',
                                          'sells': '$186M'},
                                         {'logo': 'NET',
                                          'chip': 'NET',
                                          'name': 'Cloudflare',
                                          'buys': '—',
                                          'sells': '$128M'},
                                         {'logo': 'PANW',
                                          'chip': 'PANW',
                                          'name': 'Palo Alto Networks',
                                          'buys': '—',
                                          'sells': '$94M'}],
                                'heads': [['Disclosed', 'purchases'], ['Disclosed', 'sales']],
                                'more': '+5 more with filings',
                                'note': 'Purchases and sales only · sales can be pre-planned (10b5-1)'}},
      'image_spec': {'layout': 'ledger',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'Cyber Wars',
                     'subtitle': '15 companies · insider filings · last 90 days',
                     'heads': [['Disclosed', 'purchases'], ['Disclosed', 'sales']],
                     'rows': [{'logo': 'RPD', 'chip': 'RPD', 'name': 'Rapid7', 'buys': '$2.1M', 'sells': '—'},
                              {'logo': 'S', 'chip': 'S', 'name': 'SentinelOne', 'buys': '$1.2M', 'sells': '$8.6M'},
                              {'logo': 'TENB', 'chip': 'TENB', 'name': 'Tenable', 'buys': '$640K', 'sells': '—'},
                              {'logo': 'CRWD', 'chip': 'CRWD', 'name': 'CrowdStrike', 'buys': '—', 'sells': '$186M'},
                              {'logo': 'NET', 'chip': 'NET', 'name': 'Cloudflare', 'buys': '—', 'sells': '$128M'},
                              {'logo': 'PANW',
                               'chip': 'PANW',
                               'name': 'Palo Alto Networks',
                               'buys': '—',
                               'sells': '$94M'},
                              {'logo': 'FTNT', 'chip': 'FTNT', 'name': 'Fortinet', 'buys': '—', 'sells': '$63M'},
                              {'logo': 'ZS', 'chip': 'ZS', 'name': 'Zscaler', 'buys': '—', 'sells': '$41M'},
                              {'logo': 'OKTA', 'chip': 'OKTA', 'name': 'Okta', 'buys': '—', 'sells': '$22M'},
                              {'logo': 'QLYS', 'chip': 'QLYS', 'name': 'Qualys', 'buys': '—', 'sells': '$14M'}],
                     'note': 'Purchases and sales only · sales can be pre-planned (10b5-1)',
                     'icon': 'shield',
                     'more': '+1 more with filings',
                     'footer': 'Not investment advice · Source: SEC Form 4 · filed Aug 27–Nov 24, 2026'},
      'image_post': {'title': 'Cyber Wars',
                     'paragraphs': ['The image lists 10 companies in Cyber Wars with insider purchases or sales of '
                                    'their stock disclosed on Form 4 in the last 90 days, each total in dollars as '
                                    'filed.',
                                    'Shown: Rapid7; SentinelOne; Tenable; CrowdStrike; Cloudflare; Palo Alto '
                                    'Networks; Fortinet; Zscaler; Okta; Qualys.',
                                    'Source: SEC Form 4 filings; grouping by Caydex. Members as of Nov 18, 2026.']},
      'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Aug 27–Nov 24, 2026',
      'persons': [],
      'logo_refs': [{'key': 'RPD', 'name': 'Rapid7'},
                    {'key': 'S', 'name': 'SentinelOne'},
                    {'key': 'TENB', 'name': 'Tenable'},
                    {'key': 'CRWD', 'name': 'CrowdStrike'},
                    {'key': 'NET', 'name': 'Cloudflare'},
                    {'key': 'PANW', 'name': 'Palo Alto Networks'},
                    {'key': 'FTNT', 'name': 'Fortinet'},
                    {'key': 'ZS', 'name': 'Zscaler'},
                    {'key': 'OKTA', 'name': 'Okta'},
                    {'key': 'QLYS', 'name': 'Qualys'}],
      'source_ref': 'news:theme_explainer:cyber-wars:insiders:2026-11-25',
      'x': 'Cyber Wars: insider purchases and sales disclosed on Form 4, filed Aug 27-Nov 24.\n\nVaronis: sales $9.8M',
      'bluesky': 'Cyber Wars: insider purchases and sales disclosed on Form 4, filed Aug 27-Nov 24.\n'
                 '\n'
                 'Varonis: sales $9.8M',
      'linkedin': 'Cyber Wars: insider purchases and sales disclosed on Form 4, filed Aug 27-Nov 24.\n'
                  '\n'
                  'Rapid7: about $2.1 million of disclosed purchases.\n'
                  '\n'
                  'SentinelOne: about $1.2 million of disclosed purchases and about $8.6 million of disclosed sales.\n'
                  '\n'
                  'Tenable: about $640,000 of disclosed purchases.\n'
                  '\n'
                  'CrowdStrike: about $186 million of disclosed sales.\n'
                  '\n'
                  'Cloudflare: about $128 million of disclosed sales.\n'
                  '\n'
                  'Palo Alto Networks: about $94 million of disclosed sales.\n'
                  '\n'
                  'Fortinet: about $63 million of disclosed sales.\n'
                  '\n'
                  'Zscaler: about $41 million of disclosed sales.\n'
                  '\n'
                  'Okta: about $22 million of disclosed sales.\n'
                  '\n'
                  'Qualys: about $14 million of disclosed sales.\n'
                  '\n'
                  'Varonis: about $9.8 million of disclosed sales.\n'
                  '\n'
                  "The totals are Form 4 purchases (code P) and sales (code S) of each company's stock filed Aug "
                  "27-Nov 24, in dollars as filed: shares times each line's per-share amount.\n"
                  '\n'
                  'Sales can be pre-planned under Rule 10b5-1; awards, option exercises, gifts and tax withholding '
                  'are not counted.\n'
                  '\n'
                  'No purchase or sale of the stock was disclosed at the other 4 companies checked.\n'
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'Cyber Wars: insider purchases and sales disclosed on Form 4, filed Aug 27-Nov 24.\n'
                '\n'
                'Fortinet: sales $63M\n'
                '\n'
                'Zscaler: sales $41M\n'
                '\n'
                'Okta: sales $22M\n'
                '\n'
                'Qualys: sales $14M\n'
                '\n'
                'Varonis: sales $9.8M',
      'youtube_title': 'Cyber Wars: insider purchases and sales disclosed on Form 4'},
    'theme_congress_battlefield': {'hook': 'Modern Battlefield: stock purchases disclosed by members of Congress in October.',
      'video_script': ['6 members of Congress disclosed purchases of Palantir stock in October 2026.',
                       '4 members disclosed purchases of Lockheed Martin stock, and 3 of RTX stock.',
                       "Each member is counted once per company; reports can include a spouse's or child's trades.",
                       'Reports are due within 45 days of a trade; some come later, so the trades may be older.'],
      'cards': [{'title': 'Palantir', 'body': '6 members disclosed purchases in October 2026'},
                {'title': 'Also disclosed', 'body': 'Lockheed Martin: 4 members · RTX: 3 members'},
                {'title': 'Counted once per company', 'body': 'Can include spouse and dependent-child trades'},
                {'title': 'Reporting delay', 'body': 'Due within 45 days of the trade; some are filed later'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['PLTR'],
                       'figure': '5',
                       'headline': 'Modern Battlefield',
                       'list': {'layout': 'tally',
                                'heading': 'Members of Congress who disclosed purchases',
                                'rows': [{'logo': 'PLTR', 'chip': 'PLTR', 'name': 'Palantir', 'count': '6'},
                                         {'logo': 'LMT', 'chip': 'LMT', 'name': 'Lockheed Martin', 'count': '4'},
                                         {'logo': 'RTX', 'chip': 'RTX', 'name': 'RTX', 'count': '3'},
                                         {'logo': 'GD', 'chip': 'GD', 'name': 'General Dynamics', 'count': '2'},
                                         {'logo': 'KTOS', 'chip': 'KTOS', 'name': 'Kratos', 'count': '2'}],
                                'unit': 'members',
                                'note': 'October 2026 · each member counted once per company'}},
      'image_spec': {'layout': 'tally',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'Modern Battlefield',
                     'subtitle': 'Members of Congress who disclosed purchases',
                     'unit': 'members',
                     'rows': [{'logo': 'PLTR', 'chip': 'PLTR', 'name': 'Palantir', 'count': '6'},
                              {'logo': 'LMT', 'chip': 'LMT', 'name': 'Lockheed Martin', 'count': '4'},
                              {'logo': 'RTX', 'chip': 'RTX', 'name': 'RTX', 'count': '3'},
                              {'logo': 'GD', 'chip': 'GD', 'name': 'General Dynamics', 'count': '2'},
                              {'logo': 'KTOS', 'chip': 'KTOS', 'name': 'Kratos', 'count': '2'}],
                     'note': 'October 2026 · each member counted once per company',
                     'icon': 'radar',
                     'footer': 'Not investment advice · Source: congressional disclosures · October 2026'},
      'image_post': {'title': 'Modern Battlefield',
                     'paragraphs': ['The image lists 5 companies in Modern Battlefield and how many members of '
                                    "Congress disclosed purchases of each one's stock in October 2026.",
                                    'Palantir, 6 members; Lockheed Martin, 4 members; RTX, 3 members; General '
                                    'Dynamics, 2 members; Kratos, 2 members.',
                                    'Source: congressional periodic transaction reports; grouping by Caydex. Members '
                                    'as of Nov 18, 2026.']},
      'image_footer': 'Not investment advice · Source: congressional disclosures · October 2026',
      'persons': [],
      'logo_refs': [{'key': 'PLTR', 'name': 'Palantir'},
                    {'key': 'LMT', 'name': 'Lockheed Martin'},
                    {'key': 'RTX', 'name': 'RTX'},
                    {'key': 'GD', 'name': 'General Dynamics'},
                    {'key': 'KTOS', 'name': 'Kratos'}],
      'source_ref': 'news:theme_explainer:modern-battlefield:congress:2026-11-25',
      'x': 'Modern Battlefield: stock purchases disclosed by members of Congress in October 2026.\n'
           '\n'
           'Palantir: 6 members',
      'bluesky': 'Modern Battlefield: stock purchases disclosed by members of Congress in October 2026.\n'
                 '\n'
                 'Palantir: 6 members',
      'linkedin': 'Modern Battlefield: stock purchases disclosed by members of Congress in October 2026, company by '
                  'company.\n'
                  '\n'
                  'Palantir (PLTR): 6 members of Congress disclosed purchases in October 2026.\n'
                  '\n'
                  'Lockheed Martin (LMT): 4 members of Congress disclosed purchases in October 2026.\n'
                  '\n'
                  'RTX (RTX): 3 members of Congress disclosed purchases in October 2026.\n'
                  '\n'
                  'General Dynamics (GD): 2 members of Congress disclosed purchases in October 2026.\n'
                  '\n'
                  'Kratos (KTOS): 2 members of Congress disclosed purchases in October 2026.\n'
                  '\n'
                  'Only companies with purchases disclosed by at least two members of Congress are listed.\n'
                  '\n'
                  'The counts cover periodic transaction reports disclosed during October 2026, as of Nov 25.\n'
                  '\n'
                  'Each member is counted once per company, however many reports they filed that month.\n'
                  '\n'
                  "These reports can include a spouse's or dependent child's trades, and give amounts only as "
                  'ranges.\n'
                  '\n'
                  'Reports are due within 45 days of a trade; some come later, so the trades may be older.\n'
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'Modern Battlefield: stock purchases disclosed by members of Congress in October 2026, company by '
                'company.\n'
                '\n'
                'Palantir (PLTR): 6 members of Congress disclosed purchases in October 2026.',
      'youtube_title': 'Modern Battlefield: stock purchases disclosed by members of Congress in October 2026'},
    'theme_power_machine': {'hook': 'Power the Machine: 14 of its 16 companies, ranked by market value.',
      'video_script': ['GE Vernova had the largest market value, about $171 billion, on November 24th.',
                       'NextEra Energy came next at about $152 billion, then Eaton at about $134 billion.',
                       "A company's market value is the value of all of its shares together.",
                       "This grouping is Caydex's own; its members can change."],
      'cards': [{'title': 'Largest market value', 'body': 'GE Vernova: $171B on Nov 24, 2026'},
                {'title': 'Second and third', 'body': 'NextEra Energy: $152B · Eaton: $134B'},
                {'title': 'What market value is', 'body': "All of a company's shares, valued together"},
                {'title': 'A Caydex grouping', 'body': 'Its members can change'}],
      'opening_card': {'kicker': 'INSIDE A THEME',
                       'logos': ['GEV'],
                       'figure': '14',
                       'headline': 'Power the Machine',
                       'list': {'heading': '14 of its 16 companies · market value as of Nov 24, 2026',
                                'rows': [{'logo': 'GEV',
                                          'chip': 'GEV',
                                          'label': '1',
                                          'cells': ['GE Vernova', '$171B']},
                                         {'logo': 'NEE',
                                          'chip': 'NEE',
                                          'label': '2',
                                          'cells': ['NextEra Energy', '$152B']},
                                         {'logo': 'ETN', 'chip': 'ETN', 'label': '3', 'cells': ['Eaton', '$134B']},
                                         {'logo': 'CEG',
                                          'chip': 'CEG',
                                          'label': '4',
                                          'cells': ['Constellation Energy', '$98B']}],
                                'more': '+10 more'}},
      'image_spec': {'layout': 'ranked',
                     'version': 1,
                     'kicker': 'INSIDE A THEME',
                     'title': 'Power the Machine',
                     'subtitle': '14 of its 16 companies · market value as of Nov 24, 2026',
                     'rows': [{'rank': '1',
                               'logo': 'GEV',
                               'chip': 'GEV',
                               'name': 'GE Vernova',
                               'value': '$171B',
                               'ratio': 1.0},
                              {'rank': '2',
                               'logo': 'NEE',
                               'chip': 'NEE',
                               'name': 'NextEra Energy',
                               'value': '$152B',
                               'ratio': 0.8889},
                              {'rank': '3',
                               'logo': 'ETN',
                               'chip': 'ETN',
                               'name': 'Eaton',
                               'value': '$134B',
                               'ratio': 0.7836},
                              {'rank': '4',
                               'logo': 'CEG',
                               'chip': 'CEG',
                               'name': 'Constellation Energy',
                               'value': '$98B',
                               'ratio': 0.5731},
                              {'rank': '5',
                               'logo': 'VRT',
                               'chip': 'VRT',
                               'name': 'Vertiv',
                               'value': '$52B',
                               'ratio': 0.3041},
                              {'rank': '6',
                               'logo': 'PWR',
                               'chip': 'PWR',
                               'name': 'Quanta Services',
                               'value': '$50B',
                               'ratio': 0.2924},
                              {'rank': '7',
                               'logo': 'D',
                               'chip': 'D',
                               'name': 'Dominion Energy',
                               'value': '$48B',
                               'ratio': 0.2807},
                              {'rank': '8',
                               'logo': 'VST',
                               'chip': 'VST',
                               'name': 'Vistra',
                               'value': '$45B',
                               'ratio': 0.2632},
                              {'rank': '9',
                               'logo': 'CCJ',
                               'chip': 'CCJ',
                               'name': 'Cameco',
                               'value': '$34B',
                               'ratio': 0.1988},
                              {'rank': '10',
                               'logo': 'BWXT',
                               'chip': 'BWXT',
                               'name': 'BWX Technologies',
                               'value': '$12B',
                               'ratio': 0.0702}],
                     'icon': 'bolt',
                     'more': '+4 more',
                     'footer': 'Not investment advice · Source: company filings and market data'},
      'image_post': {'title': 'Power the Machine',
                     'paragraphs': ['The image ranks 10 of the 16 companies in Power the Machine by market value as '
                                    'of Nov 24, 2026.',
                                    'Shown: GE Vernova; NextEra Energy; Eaton; Constellation Energy; Vertiv; Quanta '
                                    'Services; Dominion Energy; Vistra; Cameco; BWX Technologies.',
                                    'Source: company filings and market data; grouping by Caydex. Members as of Nov '
                                    '18, 2026.']},
      'image_footer': 'Not investment advice · Source: company filings and market data',
      'persons': [],
      'logo_refs': [{'key': 'GEV', 'name': 'GE Vernova'},
                    {'key': 'NEE', 'name': 'NextEra Energy'},
                    {'key': 'ETN', 'name': 'Eaton'},
                    {'key': 'CEG', 'name': 'Constellation Energy'},
                    {'key': 'VRT', 'name': 'Vertiv'},
                    {'key': 'PWR', 'name': 'Quanta Services'},
                    {'key': 'D', 'name': 'Dominion Energy'},
                    {'key': 'VST', 'name': 'Vistra'},
                    {'key': 'CCJ', 'name': 'Cameco'},
                    {'key': 'BWXT', 'name': 'BWX Technologies'}],
      'source_ref': 'news:theme_explainer:powering-machine:biggest:2026-11-25',
      'x': 'Power the Machine: 14 of its 16 companies by market value.\n'
           '\n'
           'GE Vernova: $171B\n'
           '\n'
           'NextEra Energy: $152B\n'
           '\n'
           'Eaton: $134B',
      'bluesky': 'Power the Machine: 14 of its 16 companies by market value.\n'
                 '\n'
                 'GE Vernova: $171B\n'
                 '\n'
                 'NextEra Energy: $152B',
      'linkedin': 'Power the Machine: 14 of its 16 companies by market value, as of Nov 24.\n'
                  '\n'
                  'GE Vernova: about $171 billion.\n'
                  '\n'
                  'NextEra Energy: about $152 billion.\n'
                  '\n'
                  'Eaton: about $134 billion.\n'
                  '\n'
                  'Constellation Energy: about $98 billion.\n'
                  '\n'
                  'Vertiv: about $52 billion.\n'
                  '\n'
                  'Quanta Services: about $50 billion.\n'
                  '\n'
                  'Dominion Energy: about $48 billion.\n'
                  '\n'
                  'Vistra: about $45 billion.\n'
                  '\n'
                  'Cameco: about $34 billion.\n'
                  '\n'
                  'BWX Technologies: about $12 billion.\n'
                  '\n'
                  'Oklo: about $9.8 billion.\n'
                  '\n'
                  'NuScale Power: about $4.4 billion.\n'
                  '\n'
                  'Centrus Energy: about $2.5 billion.\n'
                  '\n'
                  'Nano Nuclear Energy: about $1.2 billion.\n'
                  '\n'
                  "Market value is the value of all of a company's shares together, as of Nov 24.\n"
                  '\n'
                  "This grouping is Caydex's own; its members can change.",
      'tiktok': 'Power the Machine: 14 of its 16 companies by market value, as of Nov 24.\n'
                '\n'
                'Vertiv: $52B\n'
                '\n'
                'Quanta Services: $50B\n'
                '\n'
                'Dominion Energy: $48B\n'
                '\n'
                'Vistra: $45B\n'
                '\n'
                'Cameco: $34B\n'
                '\n'
                'BWX Technologies: $12B\n'
                '\n'
                'Oklo: $9.8B\n'
                '\n'
                'NuScale Power: $4.4B\n'
                '\n'
                'Centrus Energy: $2.5B\n'
                '\n'
                'Nano Nuclear Energy: $1.2B',
      'youtube_title': 'Power the Machine: 14 of its 16 companies by market value'},
}


#: news-v22 (2026-11-15): the spotlight lane's two series, pinned verbatim (regenerate only with a TEMPLATE_VERSION
#: bump — the `f13s_*` entries were regenerated on 2026-11-19 WITHOUT one, news-v22 not having shipped: the owner's
#: feedback on the rendered 13F picture put the direction first, "{filer} reduced its {co} holding"; regenerated again
#: the same day for the exit and new wording — "{filer}'s latest 13F lists no {co} shares", "+5.6M shares" — and the
#: `f13s_list*` LIST samples added; their goldens regenerated once more after the list's review: a "some" hook that
#: says the direction, no count on the cover or after "N of its holding changes", "None in this 13F"; news-v23 (the
#: restyle, the same release): the single-holding samples are gone — every 13F post is the ONE list — and the list
#: goldens regenerated: "Closed out", the ticker chips, the cover's list, share counts only). `isp_cluster` is the design's worked example (design_content step 10) WITHOUT the
#: first-line prefix (a later switch) and WITHOUT any cost per share in the narration or on a card (owner decision
#: "Captions + picture": the cost is in the caption paragraphs and on the picture's facts only); every caption field
#: is pinned for those two. The filing's cost per share never reaches `hook`, `video_script`, `cards`,
#: `opening_card` or `image_post` (pinned by `test_news_v22_the_cost_per_share_is_caption_and_picture_only`).
GOLDEN_SPOTLIGHT = {
    'isp_cluster': {'hook': "Insiders at 3 companies disclosed buying their own company's stock.",
     'video_script': ['The largest total is at Contoso: $2.7 million, by its chief executive and at least 2 other '
                      'insiders.',
                      'Insiders also disclosed purchases at Litware and Proseware.',
                      'Officers and directors must file a Form 4 within two business days of a trade.',
                      "Amounts are each filing's share count times the cost it reports for each share."],
     'cards': [{'title': 'Largest total', 'body': 'Contoso · CEO + at least 2 other insiders · $2.7M'},
               {'title': 'Also on the list', 'body': 'Litware and Proseware'},
               {'title': 'What a Form 4 is', 'body': "Filed within two business days of an insider's trade"},
               {'title': 'How the amount is counted', 'body': 'Shares times the cost of each share, per the filing'}],
     'opening_card': {'kicker': 'FILED NOV 17–18 · FORM 4',
                      'logos': ['CTSO'],
                      'headline': 'Insiders at 3 companies disclosed buying stock',
                      'list': {'heading': 'Total purchases',
                               'rows': [{'logo': 'CTSO',
                                         'chip': 'CTSO',
                                         'cells': ['Contoso', 'CEO + at least 2 other insiders', '$2.7M']},
                                        {'logo': 'LTW', 'chip': 'LTW', 'cells': ['Litware', 'Director', '$140K']},
                                        {'logo': 'PRSW', 'chip': 'PRSW', 'cells': ['Proseware', 'CFO', '$120K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED NOV 17–18 · FORM 4',
                    'title': 'Insiders at 3 companies disclosed buying stock',
                    'sections': [{'heading': 'Total purchases',
                                  'rows': [{'logo': 'CTSO',
                                            'chip': 'CTSO',
                                            'cells': ['Contoso', 'CEO + at least 2 other insiders', '$2.7M'],
                                            'detail': 'about 19K shares · $141.38–$143.85 a share, as filed'},
                                           {'logo': 'LTW',
                                            'chip': 'LTW',
                                            'cells': ['Litware', 'Director', '$140K'],
                                            'detail': '3,500 shares · $40.00 a share, as filed'},
                                           {'logo': 'PRSW',
                                            'chip': 'PRSW',
                                            'cells': ['Proseware', 'CFO', '$120K'],
                                            'detail': '4,800 shares · $25.00 a share, as filed'}]}],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 17–18, 2026'},
     'image_post': {'title': 'Insiders at 3 companies disclosed buying stock',
                    'paragraphs': ["The image lists each company's total purchases by its insiders: Contoso, CEO + "
                                   'at least 2 other insiders, $2.7M; Litware, Director, $140K; Proseware, CFO, '
                                   '$120K.',
                                   'Source: SEC Form 4 filings. Filed Nov 17–18, 2026.']},
     'persons': [],
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 17–18, 2026',
     'logo_refs': [{'key': 'CTSO', 'name': 'Contoso'},
                   {'key': 'LTW', 'name': 'Litware'},
                   {'key': 'PRSW', 'name': 'Proseware'}],
     'source_ref': 'news:insider_spotlight:CTSO+LTW+PRSW:2026-11-17',
     'x': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18.",
     'bluesky': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18.",
     'threads': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18.\n"
                '\n'
                'Contoso (CTSO): the CEO and at least 2 other insiders disclosed buying $2.7 million of stock, about '
                '19,000 shares at $141.38 to $143.85 a share, as filed.',
     'facebook': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18. The "
                 'largest total: Contoso (CTSO), $2.7 million.\n'
                 '\n'
                 'Contoso (CTSO): the CEO and at least 2 other insiders disclosed buying $2.7 million of stock, '
                 'about 19,000 shares at $141.38 to $143.85 a share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'linkedin': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18. The "
                 'largest total: Contoso (CTSO), $2.7 million.\n'
                 '\n'
                 'Contoso (CTSO): the CEO and at least 2 other insiders disclosed buying $2.7 million of stock, '
                 'about 19,000 shares at $141.38 to $143.85 a share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'instagram': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18. "
                  'The largest total: Contoso (CTSO), $2.7 million.\n'
                  '\n'
                  'Contoso (CTSO): the CEO and at least 2 other insiders disclosed buying $2.7 million of stock, '
                  'about 19,000 shares at $141.38 to $143.85 a share, as filed.\n'
                  '\n'
                  'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                  'filed.\n'
                  '\n'
                  'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                  'filed.\n'
                  '\n'
                  "A Form 4 is the report a company's officers and directors file within two business days of a "
                  "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                  '\n'
                  'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'tiktok': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18. The "
               'largest total: Contoso (CTSO), $2.7 million.\n'
               '\n'
               'Contoso (CTSO): the CEO and at least 2 other insiders disclosed buying $2.7 million of stock, about '
               '19,000 shares at $141.38 to $143.85 a share, as filed.',
     'youtube_title': "Insiders at 3 companies disclosed buying their own company's stock",
     'youtube_description': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov "
                            '17-18. The largest total: Contoso (CTSO), $2.7 million.\n'
                            '\n'
                            'Contoso (CTSO): the CEO and at least 2 other insiders disclosed buying $2.7 million of '
                            'stock, about 19,000 shares at $141.38 to $143.85 a share, as filed.\n'
                            '\n'
                            'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a '
                            'share, as filed.\n'
                            '\n'
                            'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a '
                            'share, as filed.\n'
                            '\n'
                            "A Form 4 is the report a company's officers and directors file within two business days "
                            "of a trade. Amounts are each filing's share count times the cost it reports for each "
                            'share.\n'
                            '\n'
                            'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?'},
    'isp_single': {'hook': "Insiders at 3 companies disclosed buying their own company's stock.",
     'video_script': ['The largest total is at Fabrikam: $612,500, by its chief financial officer.',
                      'Insiders also disclosed purchases at Litware and Proseware.',
                      'A Form 4 is the report officers and directors file on their own trades.',
                      "Amounts are each filing's share count times the cost it reports for each share."],
     'cards': [{'title': 'Largest total', 'body': 'Fabrikam · CFO · $612.5K'},
               {'title': 'Also on the list', 'body': 'Litware and Proseware'},
               {'title': 'What a Form 4 is', 'body': 'The report officers and directors file on their own trades'},
               {'title': 'How the amount is counted', 'body': 'Shares times the cost of each share, per the filing'}],
     'opening_card': {'kicker': 'FILED NOV 16 · FORM 4',
                      'logos': ['FBKM'],
                      'headline': 'Insiders at 3 companies disclosed buying stock',
                      'list': {'heading': 'Total purchases',
                               'rows': [{'logo': 'FBKM', 'chip': 'FBKM', 'cells': ['Fabrikam', 'CFO', '$612.5K']},
                                        {'logo': 'LTW', 'chip': 'LTW', 'cells': ['Litware', 'Director', '$140K']},
                                        {'logo': 'PRSW', 'chip': 'PRSW', 'cells': ['Proseware', 'CFO', '$120K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED NOV 16 · FORM 4',
                    'title': 'Insiders at 3 companies disclosed buying stock',
                    'sections': [{'heading': 'Total purchases',
                                  'rows': [{'logo': 'FBKM',
                                            'chip': 'FBKM',
                                            'cells': ['Fabrikam', 'CFO', '$612.5K'],
                                            'detail': '25K shares · $24.10–$24.90 a share, as filed'},
                                           {'logo': 'LTW',
                                            'chip': 'LTW',
                                            'cells': ['Litware', 'Director', '$140K'],
                                            'detail': '3,500 shares · $40.00 a share, as filed'},
                                           {'logo': 'PRSW',
                                            'chip': 'PRSW',
                                            'cells': ['Proseware', 'CFO', '$120K'],
                                            'detail': '4,800 shares · $25.00 a share, as filed'}]}],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 16, 2026'},
     'image_post': {'title': 'Insiders at 3 companies disclosed buying stock',
                    'paragraphs': ["The image lists each company's total purchases by its insiders: Fabrikam, CFO, "
                                   '$612.5K; Litware, Director, $140K; Proseware, CFO, $120K.',
                                   'Source: SEC Form 4 filings. Filed Nov 16, 2026.']},
     'persons': [],
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 16, 2026',
     'logo_refs': [{'key': 'FBKM', 'name': 'Fabrikam'},
                   {'key': 'LTW', 'name': 'Litware'},
                   {'key': 'PRSW', 'name': 'Proseware'}],
     'source_ref': 'news:insider_spotlight:FBKM+LTW+PRSW:2026-11-14',
     'x': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16.",
     'bluesky': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16.",
     'threads': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16. The "
                'largest total: Fabrikam (FBKM), $612,500.\n'
                '\n'
                'Fabrikam (FBKM): the CFO disclosed buying $612,500 of stock, 25,000 shares at $24.10 to $24.90 a '
                'share, as filed.',
     'facebook': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16. The "
                 'largest total: Fabrikam (FBKM), $612,500.\n'
                 '\n'
                 'Fabrikam (FBKM): the CFO disclosed buying $612,500 of stock, 25,000 shares at $24.10 to $24.90 a '
                 'share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 "The filings give trade dates from Nov 5 to Nov 16. A Form 4 is the report a company's officers and "
                 "directors file on their own trades. Amounts are each filing's share count times the cost it "
                 'reports for each share.\n'
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'linkedin': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16. The "
                 'largest total: Fabrikam (FBKM), $612,500.\n'
                 '\n'
                 'Fabrikam (FBKM): the CFO disclosed buying $612,500 of stock, 25,000 shares at $24.10 to $24.90 a '
                 'share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 "The filings give trade dates from Nov 5 to Nov 16. A Form 4 is the report a company's officers and "
                 "directors file on their own trades. Amounts are each filing's share count times the cost it "
                 'reports for each share.\n'
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'instagram': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16. The "
                  'largest total: Fabrikam (FBKM), $612,500.\n'
                  '\n'
                  'Fabrikam (FBKM): the CFO disclosed buying $612,500 of stock, 25,000 shares at $24.10 to $24.90 a '
                  'share, as filed.\n'
                  '\n'
                  'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                  'filed.\n'
                  '\n'
                  'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                  'filed.\n'
                  '\n'
                  "The filings give trade dates from Nov 5 to Nov 16. A Form 4 is the report a company's officers "
                  "and directors file on their own trades. Amounts are each filing's share count times the cost it "
                  'reports for each share.\n'
                  '\n'
                  'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'tiktok': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16. The "
               'largest total: Fabrikam (FBKM), $612,500.\n'
               '\n'
               'Fabrikam (FBKM): the CFO disclosed buying $612,500 of stock, 25,000 shares at $24.10 to $24.90 a '
               'share, as filed.',
     'youtube_title': "Insiders at 3 companies disclosed buying their own company's stock",
     'youtube_description': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov "
                            '16. The largest total: Fabrikam (FBKM), $612,500.\n'
                            '\n'
                            'Fabrikam (FBKM): the CFO disclosed buying $612,500 of stock, 25,000 shares at $24.10 to '
                            '$24.90 a share, as filed.\n'
                            '\n'
                            'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a '
                            'share, as filed.\n'
                            '\n'
                            'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a '
                            'share, as filed.\n'
                            '\n'
                            "The filings give trade dates from Nov 5 to Nov 16. A Form 4 is the report a company's "
                            "officers and directors file on their own trades. Amounts are each filing's share count "
                            'times the cost it reports for each share.\n'
                            '\n'
                            'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?'},
    'isp_indirect': {'hook': "Insiders at 3 companies disclosed buying their own company's stock.",
     'video_script': ['The largest total is at Northwind: $1.6 million, by a director and at least one other '
                      'insider.',
                      'Insiders also disclosed purchases at Litware and Proseware.',
                      'Some of the shares are reported as held indirectly.',
                      'Officers and directors must file a Form 4 within two business days of a trade.'],
     'cards': [{'title': 'Largest total', 'body': 'Northwind · Director + at least one other insider · $1.6M'},
               {'title': 'Also on the list', 'body': 'Litware and Proseware'},
               {'title': 'Partly held indirectly', 'body': 'Some of the shares, per the filing'},
               {'title': 'What a Form 4 is', 'body': "Filed within two business days of an insider's trade"}],
     'opening_card': {'kicker': 'FILED NOV 19–20 · FORM 4',
                      'logos': ['NWND'],
                      'headline': 'Insiders at 3 companies disclosed buying stock',
                      'list': {'heading': 'Total purchases',
                               'rows': [{'logo': 'NWND',
                                         'chip': 'NWND',
                                         'cells': ['Northwind', 'Director + at least one other insider', '$1.6M']},
                                        {'logo': 'LTW', 'chip': 'LTW', 'cells': ['Litware', 'Director', '$140K']},
                                        {'logo': 'PRSW', 'chip': 'PRSW', 'cells': ['Proseware', 'CFO', '$120K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED NOV 19–20 · FORM 4',
                    'title': 'Insiders at 3 companies disclosed buying stock',
                    'sections': [{'heading': 'Total purchases',
                                  'rows': [{'logo': 'NWND',
                                            'chip': 'NWND',
                                            'cells': ['Northwind', 'Director + at least one other insider', '$1.6M'],
                                            'detail': '62K shares · $25.00 a share, as filed'},
                                           {'logo': 'LTW',
                                            'chip': 'LTW',
                                            'cells': ['Litware', 'Director', '$140K'],
                                            'detail': '3,500 shares · $40.00 a share, as filed'},
                                           {'logo': 'PRSW',
                                            'chip': 'PRSW',
                                            'cells': ['Proseware', 'CFO', '$120K'],
                                            'detail': '4,800 shares · $25.00 a share, as filed'}]}],
                    'notes': ['Some of the shares in these purchases are reported as held indirectly.'],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 19–20, 2026'},
     'image_post': {'title': 'Insiders at 3 companies disclosed buying stock',
                    'paragraphs': ["The image lists each company's total purchases by its insiders: Northwind, "
                                   'Director + at least one other insider, $1.6M; Litware, Director, $140K; '
                                   'Proseware, CFO, $120K.',
                                   'Some of the shares in these purchases are reported as held indirectly.',
                                   'Source: SEC Form 4 filings. Filed Nov 19–20, 2026.']},
     'persons': [],
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Nov 19–20, 2026',
     'logo_refs': [{'key': 'NWND', 'name': 'Northwind'},
                   {'key': 'LTW', 'name': 'Litware'},
                   {'key': 'PRSW', 'name': 'Proseware'}],
     'source_ref': 'news:insider_spotlight:NWND+LTW+PRSW:2026-11-19',
     'x': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20.",
     'bluesky': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20.",
     'threads': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20.\n"
                '\n'
                'Northwind (NWND): a director and at least one other insider disclosed buying $1.6 million of stock, '
                '62,000 shares at $25.00 a share, as filed.',
     'facebook': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20. The "
                 'largest total: Northwind (NWND), $1.6 million.\n'
                 '\n'
                 'Northwind (NWND): a director and at least one other insider disclosed buying $1.6 million of '
                 'stock, 62,000 shares at $25.00 a share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Some of the shares are reported as held indirectly.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'linkedin': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20. The "
                 'largest total: Northwind (NWND), $1.6 million.\n'
                 '\n'
                 'Northwind (NWND): a director and at least one other insider disclosed buying $1.6 million of '
                 'stock, 62,000 shares at $25.00 a share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Some of the shares are reported as held indirectly.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'instagram': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20. "
                  'The largest total: Northwind (NWND), $1.6 million.\n'
                  '\n'
                  'Northwind (NWND): a director and at least one other insider disclosed buying $1.6 million of '
                  'stock, 62,000 shares at $25.00 a share, as filed.\n'
                  '\n'
                  'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                  'filed.\n'
                  '\n'
                  'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                  'filed.\n'
                  '\n'
                  'Some of the shares are reported as held indirectly.\n'
                  '\n'
                  "A Form 4 is the report a company's officers and directors file within two business days of a "
                  "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                  '\n'
                  'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'tiktok': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 19-20. The "
               'largest total: Northwind (NWND), $1.6 million.\n'
               '\n'
               'Northwind (NWND): a director and at least one other insider disclosed buying $1.6 million of stock, '
               '62,000 shares at $25.00 a share, as filed.',
     'youtube_title': "Insiders at 3 companies disclosed buying their own company's stock",
     'youtube_description': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov "
                            '19-20. The largest total: Northwind (NWND), $1.6 million.\n'
                            '\n'
                            'Northwind (NWND): a director and at least one other insider disclosed buying $1.6 '
                            'million of stock, 62,000 shares at $25.00 a share, as filed.\n'
                            '\n'
                            'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a '
                            'share, as filed.\n'
                            '\n'
                            'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a '
                            'share, as filed.\n'
                            '\n'
                            'Some of the shares are reported as held indirectly.\n'
                            '\n'
                            "A Form 4 is the report a company's officers and directors file within two business days "
                            "of a trade. Amounts are each filing's share count times the cost it reports for each "
                            'share.\n'
                            '\n'
                            'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?'},
    'isp_named_outside_window': {'hook': "Insiders at 3 companies disclosed buying their own company's stock.",
     'video_script': ['The largest total is at Tailspin Toys: $3.6 million, by its chief executive and at least one '
                      'other insider.',
                      'Insiders also disclosed purchases at Litware and Proseware.',
                      'Officers and directors must file a Form 4 within two business days of a trade.',
                      "Amounts are each filing's share count times the cost it reports for each share."],
     'cards': [{'title': 'Largest total', 'body': 'Tailspin Toys · CEO + at least one other insider · $3.6M'},
               {'title': 'Also on the list', 'body': 'Litware and Proseware'},
               {'title': 'What a Form 4 is', 'body': "Filed within two business days of an insider's trade"},
               {'title': 'How the amount is counted', 'body': 'Shares times the cost of each share, per the filing'}],
     'opening_card': {'kicker': 'FILED DEC 31–JAN 4 · FORM 4',
                      'logos': ['TLSP'],
                      'headline': 'Insiders at 3 companies disclosed buying stock',
                      'list': {'heading': 'Total purchases',
                               'rows': [{'logo': 'TLSP',
                                         'chip': 'TLSP',
                                         'cells': ['Tailspin Toys', 'CEO + at least one other insider', '$3.6M']},
                                        {'logo': 'LTW', 'chip': 'LTW', 'cells': ['Litware', 'Director', '$140K']},
                                        {'logo': 'PRSW', 'chip': 'PRSW', 'cells': ['Proseware', 'CFO', '$120K']}]}},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED DEC 31–JAN 4 · FORM 4',
                    'title': 'Insiders at 3 companies disclosed buying stock',
                    'sections': [{'heading': 'Total purchases',
                                  'rows': [{'logo': 'TLSP',
                                            'chip': 'TLSP',
                                            'cells': ['Tailspin Toys', 'CEO + at least one other insider', '$3.6M'],
                                            'detail': '125K shares · $28.75–$29.25 a share, as filed'},
                                           {'logo': 'LTW',
                                            'chip': 'LTW',
                                            'cells': ['Litware', 'Director', '$140K'],
                                            'detail': '3,500 shares · $40.00 a share, as filed'},
                                           {'logo': 'PRSW',
                                            'chip': 'PRSW',
                                            'cells': ['Proseware', 'CFO', '$120K'],
                                            'detail': '4,800 shares · $25.00 a share, as filed'}]}],
                    'footer': 'Not investment advice · Source: SEC Form 4 · filed Dec 31, 2026–Jan 4, 2027'},
     'image_post': {'title': 'Insiders at 3 companies disclosed buying stock',
                    'paragraphs': ["The image lists each company's total purchases by its insiders: Tailspin Toys, "
                                   'CEO + at least one other insider, $3.6M; Litware, Director, $140K; Proseware, '
                                   'CFO, $120K.',
                                   'Source: SEC Form 4 filings. Filed Dec 31, 2026–Jan 4, 2027.']},
     'persons': ['Jordan Avery'],
     'image_footer': 'Not investment advice · Source: SEC Form 4 · filed Dec 31, 2026–Jan 4, 2027',
     'logo_refs': [{'key': 'TLSP', 'name': 'Tailspin Toys'},
                   {'key': 'LTW', 'name': 'Litware'},
                   {'key': 'PRSW', 'name': 'Proseware'}],
     'source_ref': 'news:insider_spotlight:TLSP+LTW+PRSW:2026-12-30',
     'x': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec 31, 2026-Jan 4, "
          '2027.',
     'bluesky': "Insiders at 3 companies disclosed buying their own company's stock.",
     'threads': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec 31, "
                '2026-Jan 4, 2027.\n'
                '\n'
                'Tailspin Toys (TLSP): the CEO and at least one other insider disclosed buying $3.6 million of '
                'stock, 125,000 shares at $28.75 to $29.25 a share, as filed.',
     'facebook': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec 31, "
                 '2026-Jan 4, 2027. The largest total: Tailspin Toys (TLSP), $3.6 million.\n'
                 '\n'
                 'Tailspin Toys (TLSP): the CEO, Jordan Avery, and at least one other insider disclosed buying $3.6 '
                 'million of stock, 125,000 shares at $28.75 to $29.25 a share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'linkedin': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec 31, "
                 '2026-Jan 4, 2027. The largest total: Tailspin Toys (TLSP), $3.6 million.\n'
                 '\n'
                 'Tailspin Toys (TLSP): the CEO, Jordan Avery, and at least one other insider disclosed buying $3.6 '
                 'million of stock, 125,000 shares at $28.75 to $29.25 a share, as filed.\n'
                 '\n'
                 'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                 'filed.\n'
                 '\n'
                 'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                 'filed.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business days of a "
                 "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                 '\n'
                 'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'instagram': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec 31, "
                  '2026-Jan 4, 2027. The largest total: Tailspin Toys (TLSP), $3.6 million.\n'
                  '\n'
                  'Tailspin Toys (TLSP): the CEO, Jordan Avery, and at least one other insider disclosed buying $3.6 '
                  'million of stock, 125,000 shares at $28.75 to $29.25 a share, as filed.\n'
                  '\n'
                  'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as '
                  'filed.\n'
                  '\n'
                  'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as '
                  'filed.\n'
                  '\n'
                  "A Form 4 is the report a company's officers and directors file within two business days of a "
                  "trade. Amounts are each filing's share count times the cost it reports for each share.\n"
                  '\n'
                  'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?',
     'tiktok': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec 31, 2026-Jan "
               '4, 2027. The largest total: Tailspin Toys (TLSP), $3.6 million.\n'
               '\n'
               'Tailspin Toys (TLSP): the CEO, Jordan Avery, and at least one other insider disclosed buying $3.6 '
               'million of stock, 125,000 shares at $28.75 to $29.25 a share, as filed.',
     'youtube_title': "Insiders at 3 companies disclosed buying their own company's stock",
     'youtube_description': "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Dec "
                            '31, 2026-Jan 4, 2027. The largest total: Tailspin Toys (TLSP), $3.6 million.\n'
                            '\n'
                            'Tailspin Toys (TLSP): the CEO, Jordan Avery, and at least one other insider disclosed '
                            'buying $3.6 million of stock, 125,000 shares at $28.75 to $29.25 a share, as filed.\n'
                            '\n'
                            'Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a '
                            'share, as filed.\n'
                            '\n'
                            'Proseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a '
                            'share, as filed.\n'
                            '\n'
                            "A Form 4 is the report a company's officers and directors file within two business days "
                            "of a trade. Amounts are each filing's share count times the cost it reports for each "
                            'share.\n'
                            '\n'
                            'Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?'},
    'f13s_list2': {'hook': "Fabrikam Capital's latest 13F: it reduced 1 holding and increased 1.",
      'video_script': ['Compared with the quarter before, it reduced its Northwind holding.',
                       'Compared with the quarter before, it also increased its Litware holding.',
                       "A 13F shows holdings at the quarter's end, not when shares changed hands.",
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'Reduced', 'body': 'Northwind'},
                {'title': 'Increased', 'body': 'Litware'},
                {'title': 'What a 13F shows', 'body': "Holdings at the quarter's end, not trade dates"},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['13F-FILER'],
                       'headline': "Fabrikam Capital's latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'NWT',
                                          'chip': 'NWT',
                                          'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                         {'logo': 'LTW',
                                          'chip': 'LTW',
                                          'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Fabrikam Capital's latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'NWT',
                                             'chip': 'NWT',
                                             'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                            {'logo': 'LTW',
                                             'chip': 'LTW',
                                             'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']}]}],
                     'footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026'},
      'image_post': {'title': "Fabrikam Capital's latest 13F",
                     'paragraphs': ["It lists the filing's 2 holding changes from the quarter before: 1 reduced, 1 "
                                    'increased.',
                                    'Northwind, reduced to 41.9M shares from 60M; Litware, increased to 12.5M shares '
                                    'from 8M.',
                                    'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, 2026.']},
      'persons': [],
      'image_footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026',
      'logo_refs': [{'key': 'NWT', 'name': 'Northwind'},
                    {'key': 'LTW', 'name': 'Litware'},
                    {'key': '13F-FILER', 'name': 'Fabrikam Capital'}],
      'source_ref': 'news:thirteen_f_spotlight:0009999902:2026-Q3:NWT',
      'x': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.",
      'bluesky': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.",
      'threads': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.\n"
                 '\n'
                 'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                 '\n'
                 'Litware (LTW): increased to 12.5 million shares, from 8 million.',
      'facebook': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.\n"
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'linkedin': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.\n"
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'instagram': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.\n"
                   '\n'
                   'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                   '\n'
                   'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                   '\n'
                   'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                   '\n'
                   "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                   'show when shares changed hands.\n'
                   '\n'
                   'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.\n"
                '\n'
                'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.',
      'youtube_title': "Fabrikam Capital's latest 13F: 2 holding changes",
      'youtube_description': "Fabrikam Capital's latest 13F: 1 holding reduced, 1 increased.\n"
                             '\n'
                             'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                             '\n'
                             'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                             '\n'
                             'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                             '\n'
                             "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it "
                             'does not show when shares changed hands.\n'
                             '\n'
                             'What do you look at first in a 13F: new holdings, or changes in share counts?'},
    'f13s_list5': {'hook': "5 of Fabrikam Capital's holding changes, including 2 reduced and 1 new.",
      'video_script': ['Compared with the quarter before, it reduced its holdings of Northwind and Contoso.',
                       'Compared with the quarter before, it also reported a new Adventure Works holding.',
                       'Compared with the quarter before, it also increased its Litware holding.',
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'Reduced', 'body': 'Northwind and Contoso'},
                {'title': 'New holding', 'body': 'Adventure Works'},
                {'title': 'Increased', 'body': 'Litware'},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['13F-FILER'],
                       'headline': "Fabrikam Capital's latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'NWT',
                                          'chip': 'NWT',
                                          'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                         {'logo': 'ADVW',
                                          'chip': 'ADVW',
                                          'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                         {'logo': 'LTW',
                                          'chip': 'LTW',
                                          'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']}],
                                'more': '+2 more changes in the caption'}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Fabrikam Capital's latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'NWT',
                                             'chip': 'NWT',
                                             'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                            {'logo': 'ADVW',
                                             'chip': 'ADVW',
                                             'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                            {'logo': 'LTW',
                                             'chip': 'LTW',
                                             'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']},
                                            {'logo': 'WGB',
                                             'chip': 'WGB',
                                             'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']},
                                            {'logo': 'CTSO',
                                             'chip': 'CTSO',
                                             'cells': ['Contoso', 'Reduced to 2M shares', '−1M']}]}],
                     'footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026'},
      'image_post': {'title': "Fabrikam Capital's latest 13F",
                     'paragraphs': ["It lists 5 of the filing's holding changes from the quarter before: 2 reduced, 1 "
                                    'new, 1 increased, 1 closed out.',
                                    'Northwind, reduced to 41.9M shares from 60M; Adventure Works, a new holding of '
                                    '5.6M shares; Litware, increased to 12.5M shares from 8M; Woodgrove Bank, closed '
                                    'out, from 4.3M shares; Contoso, reduced to 2M shares from 3M.',
                                    'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, 2026.']},
      'persons': [],
      'image_footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026',
      'logo_refs': [{'key': 'NWT', 'name': 'Northwind'},
                    {'key': 'ADVW', 'name': 'Adventure Works'},
                    {'key': 'LTW', 'name': 'Litware'},
                    {'key': 'WGB', 'name': 'Woodgrove Bank'},
                    {'key': 'CTSO', 'name': 'Contoso'},
                    {'key': '13F-FILER', 'name': 'Fabrikam Capital'}],
      'source_ref': 'news:thirteen_f_spotlight:0009999902:2026-Q3:NWT',
      'x': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 closed out.",
      'bluesky': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 closed "
                 'out.',
      'threads': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 closed "
                 'out.\n'
                 '\n'
                 'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                 '\n'
                 'Contoso (CTSO): reduced to 2 million shares, from 3 million.',
      'facebook': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 closed "
                  'out.\n'
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                  '\n'
                  'Contoso (CTSO): reduced to 2 million shares, from 3 million.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'linkedin': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 closed "
                  'out.\n'
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                  '\n'
                  'Contoso (CTSO): reduced to 2 million shares, from 3 million.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'instagram': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 "
                   'closed out.\n'
                   '\n'
                   'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                   '\n'
                   'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                   '\n'
                   'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                   '\n'
                   'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                   '\n'
                   'Contoso (CTSO): reduced to 2 million shares, from 3 million.\n'
                   '\n'
                   'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                   '\n'
                   'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                   '\n'
                   "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                   'show when shares changed hands.\n'
                   '\n'
                   'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 increased, 1 closed "
                'out.\n'
                '\n'
                'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                '\n'
                'Contoso (CTSO): reduced to 2 million shares, from 3 million.',
      'youtube_title': "Fabrikam Capital's latest 13F: 5 of its holding changes",
      'youtube_description': "5 of the holding changes in Fabrikam Capital's latest 13F: 2 reduced, 1 new, 1 "
                             'increased, 1 closed out.\n'
                             '\n'
                             'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                             '\n'
                             'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                             '\n'
                             'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                             '\n'
                             'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                             '\n'
                             'Contoso (CTSO): reduced to 2 million shares, from 3 million.\n'
                             '\n'
                             'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                             '\n'
                             'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                             '\n'
                             "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it "
                             'does not show when shares changed hands.\n'
                             '\n'
                             'What do you look at first in a 13F: new holdings, or changes in share counts?'},
    'f13s_list_truncated': {'hook': "5 of Fabrikam Capital's holding changes: 3 reduced, 1 new, 1 increased.",
      'video_script': ['Compared with the quarter before, it reduced its holdings of Northwind, Tailspin Toys and '
                       'Wide World Importers.',
                       'Compared with the quarter before, it also reported a new Adventure Works holding.',
                       'Compared with the quarter before, it also increased its Litware holding.',
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'Reduced', 'body': 'Northwind, Tailspin Toys and Wide World Importers'},
                {'title': 'New holding', 'body': 'Adventure Works'},
                {'title': 'Increased', 'body': 'Litware'},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['13F-FILER'],
                       'headline': "Fabrikam Capital's latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'NWT',
                                          'chip': 'NWT',
                                          'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                         {'logo': 'ADVW',
                                          'chip': 'ADVW',
                                          'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                         {'logo': 'LTW',
                                          'chip': 'LTW',
                                          'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']}],
                                'more': '+2 more changes in the caption'}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Fabrikam Capital's latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'NWT',
                                             'chip': 'NWT',
                                             'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                            {'logo': 'ADVW',
                                             'chip': 'ADVW',
                                             'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                            {'logo': 'LTW',
                                             'chip': 'LTW',
                                             'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']},
                                            {'logo': 'TLSP',
                                             'chip': 'TLSP',
                                             'cells': ['Tailspin Toys', 'Reduced to 2M shares', '−1M']},
                                            {'logo': 'WWI',
                                             'chip': 'WWI',
                                             'cells': ['Wide World Importers', 'Reduced to 2M shares', '−1M']}]}],
                     'footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026'},
      'image_post': {'title': "Fabrikam Capital's latest 13F",
                     'paragraphs': ["It lists 5 of the filing's holding changes from the quarter before: 3 reduced, 1 "
                                    'new, 1 increased.',
                                    'Northwind, reduced to 41.9M shares from 60M; Adventure Works, a new holding of '
                                    '5.6M shares; Litware, increased to 12.5M shares from 8M; Tailspin Toys, reduced '
                                    'to 2M shares from 3M; Wide World Importers, reduced to 2M shares from 3M.',
                                    'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, 2026.']},
      'persons': [],
      'image_footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026',
      'logo_refs': [{'key': 'NWT', 'name': 'Northwind'},
                    {'key': 'ADVW', 'name': 'Adventure Works'},
                    {'key': 'LTW', 'name': 'Litware'},
                    {'key': 'TLSP', 'name': 'Tailspin Toys'},
                    {'key': 'WWI', 'name': 'Wide World Importers'},
                    {'key': '13F-FILER', 'name': 'Fabrikam Capital'}],
      'source_ref': 'news:thirteen_f_spotlight:0009999902:2026-Q3:NWT',
      'x': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.",
      'bluesky': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.",
      'threads': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.\n"
                 '\n'
                 'Tailspin Toys (TLSP): reduced to 2 million shares, from 3 million.\n'
                 '\n'
                 'Wide World Importers (WWI): reduced to 2 million shares, from 3 million.',
      'facebook': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.\n"
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                  '\n'
                  'Tailspin Toys (TLSP): reduced to 2 million shares, from 3 million.\n'
                  '\n'
                  'Wide World Importers (WWI): reduced to 2 million shares, from 3 million.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'linkedin': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.\n"
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                  '\n'
                  'Tailspin Toys (TLSP): reduced to 2 million shares, from 3 million.\n'
                  '\n'
                  'Wide World Importers (WWI): reduced to 2 million shares, from 3 million.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'instagram': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.\n"
                   '\n'
                   'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                   '\n'
                   'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                   '\n'
                   'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                   '\n'
                   'Tailspin Toys (TLSP): reduced to 2 million shares, from 3 million.\n'
                   '\n'
                   'Wide World Importers (WWI): reduced to 2 million shares, from 3 million.\n'
                   '\n'
                   'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                   '\n'
                   'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                   '\n'
                   "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                   'show when shares changed hands.\n'
                   '\n'
                   'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 increased.\n"
                '\n'
                'Tailspin Toys (TLSP): reduced to 2 million shares, from 3 million.\n'
                '\n'
                'Wide World Importers (WWI): reduced to 2 million shares, from 3 million.',
      'youtube_title': "Fabrikam Capital's latest 13F: 5 of its holding changes",
      'youtube_description': "5 of the holding changes in Fabrikam Capital's latest 13F: 3 reduced, 1 new, 1 "
                             'increased.\n'
                             '\n'
                             'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                             '\n'
                             'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                             '\n'
                             'Litware (LTW): increased to 12.5 million shares, from 8 million.\n'
                             '\n'
                             'Tailspin Toys (TLSP): reduced to 2 million shares, from 3 million.\n'
                             '\n'
                             'Wide World Importers (WWI): reduced to 2 million shares, from 3 million.\n'
                             '\n'
                             'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                             '\n'
                             'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                             '\n'
                             "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it "
                             'does not show when shares changed hands.\n'
                             '\n'
                             'What do you look at first in a 13F: new holdings, or changes in share counts?'},
    'f13s_list_amended': {'hook': "Fabrikam Capital's latest 13F: it reported 1 new holding and closed out 2.",
      'video_script': ['Compared with the quarter before, it reported a new Adventure Works holding.',
                       'Compared with the quarter before, it also closed out its holdings of Woodgrove Bank and '
                       'Fourth Coffee.',
                       "A 13F shows holdings at the quarter's end, not when shares changed hands.",
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'New holding', 'body': 'Adventure Works'},
                {'title': 'Closed out', 'body': 'Woodgrove Bank and Fourth Coffee'},
                {'title': 'What a 13F shows', 'body': "Holdings at the quarter's end, not trade dates"},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['13F-FILER'],
                       'headline': "Fabrikam Capital's latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'ADVW',
                                          'chip': 'ADVW',
                                          'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                         {'logo': 'WGB',
                                          'chip': 'WGB',
                                          'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']},
                                         {'logo': 'FRCO',
                                          'chip': 'FRCO',
                                          'cells': ['Fourth Coffee', 'Closed out', '−1.3M']}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Fabrikam Capital's latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'ADVW',
                                             'chip': 'ADVW',
                                             'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                            {'logo': 'WGB',
                                             'chip': 'WGB',
                                             'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']},
                                            {'logo': 'FRCO',
                                             'chip': 'FRCO',
                                             'cells': ['Fourth Coffee', 'Closed out', '−1.3M']}]}],
                     'footer': 'Not investment advice · Source: SEC Form 13F · amended Nov 18, 2026'},
      'image_post': {'title': "Fabrikam Capital's latest 13F",
                     'paragraphs': ["It lists the filing's 3 holding changes from the quarter before: 1 new, 2 closed "
                                    'out. Share counts are rounded.',
                                    'Adventure Works, a new holding of 5.6M shares; Woodgrove Bank, closed out, from '
                                    '4.3M shares; Fourth Coffee, closed out, from about 1.3M shares.',
                                    'Source: SEC Form 13F-HR/A. Quarter ended Sep 30, 2026 · filed Nov 12, 2026 · '
                                    'amended Nov 18, 2026.']},
      'persons': [],
      'image_footer': 'Not investment advice · Source: SEC Form 13F · amended Nov 18, 2026',
      'logo_refs': [{'key': 'ADVW', 'name': 'Adventure Works'},
                    {'key': 'WGB', 'name': 'Woodgrove Bank'},
                    {'key': 'FRCO', 'name': 'Fourth Coffee'},
                    {'key': '13F-FILER', 'name': 'Fabrikam Capital'}],
      'source_ref': 'news:thirteen_f_spotlight:0009999902:2026-Q3:ADVW',
      'x': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
           '\n'
           'Adventure Works (ADVW): a new holding of 5.6 million shares.',
      'bluesky': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.",
      'threads': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
                 '\n'
                 'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                 '\n'
                 'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.',
      'facebook': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                  '\n'
                  'Fourth Coffee (FRCO): closed out, from about 1.3 million shares the quarter before.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30; it was filed on Nov 12 and amended on Nov 18.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'linkedin': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                  '\n'
                  'Fourth Coffee (FRCO): closed out, from about 1.3 million shares the quarter before.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30; it was filed on Nov 12 and amended on Nov 18.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'instagram': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
                   '\n'
                   'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                   '\n'
                   'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                   '\n'
                   'Fourth Coffee (FRCO): closed out, from about 1.3 million shares the quarter before.\n'
                   '\n'
                   'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                   '\n'
                   'The filing covers the quarter that ended Sep 30; it was filed on Nov 12 and amended on Nov 18.\n'
                   '\n'
                   "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                   'show when shares changed hands.\n'
                   '\n'
                   'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
                '\n'
                'Adventure Works (ADVW): a new holding of 5.6 million shares.',
      'youtube_title': "Fabrikam Capital's latest 13F: 3 holding changes",
      'youtube_description': "Fabrikam Capital's latest 13F: 1 new holding, 2 closed out.\n"
                             '\n'
                             'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                             '\n'
                             'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                             '\n'
                             'Fourth Coffee (FRCO): closed out, from about 1.3 million shares the quarter before.\n'
                             '\n'
                             'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                             '\n'
                             'The filing covers the quarter that ended Sep 30; it was filed on Nov 12 and amended on '
                             'Nov 18.\n'
                             '\n'
                             "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it "
                             'does not show when shares changed hands.\n'
                             '\n'
                             'What do you look at first in a 13F: new holdings, or changes in share counts?'},
    'f13s_list_rounded': {'hook': "3 of Proseware Advisors' holding changes: 1 reduced, 1 increased, 1 closed out.",
      'video_script': ['Compared with the quarter before, it reduced its Northwind holding.',
                       'Compared with the quarter before, it also increased its Litware holding.',
                       'Compared with the quarter before, it also closed out its Woodgrove Bank holding.',
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'Reduced', 'body': 'Northwind'},
                {'title': 'Increased', 'body': 'Litware'},
                {'title': 'Closed out', 'body': 'Woodgrove Bank'},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['13F-FILER'],
                       'headline': "Proseware Advisors' latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'NWT',
                                          'chip': 'NWT',
                                          'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.2M']},
                                         {'logo': 'LTW',
                                          'chip': 'LTW',
                                          'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']},
                                         {'logo': 'WGB',
                                          'chip': 'WGB',
                                          'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']}]}},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Proseware Advisors' latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'NWT',
                                             'chip': 'NWT',
                                             'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.2M']},
                                            {'logo': 'LTW',
                                             'chip': 'LTW',
                                             'cells': ['Litware', 'Increased to 12.5M shares', '+4.5M']},
                                            {'logo': 'WGB',
                                             'chip': 'WGB',
                                             'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']}]}],
                     'footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026'},
      'image_post': {'title': "Proseware Advisors' latest 13F",
                     'paragraphs': ["It lists 3 of the filing's holding changes from the quarter before: 1 reduced, 1 "
                                    'increased, 1 closed out. Share counts are rounded.',
                                    'Northwind, reduced to about 41.9M shares from about 60.1M; Litware, increased to '
                                    'about 12.5M shares from about 8M; Woodgrove Bank, closed out, from about 4.3M '
                                    'shares.',
                                    'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, 2026.']},
      'persons': [],
      'image_footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026',
      'logo_refs': [{'key': 'NWT', 'name': 'Northwind'},
                    {'key': 'LTW', 'name': 'Litware'},
                    {'key': 'WGB', 'name': 'Woodgrove Bank'},
                    {'key': '13F-FILER', 'name': 'Proseware Advisors'}],
      'source_ref': 'news:thirteen_f_spotlight:0009999902:2026-Q3:NWT',
      'x': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 closed out.",
      'bluesky': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 closed out.",
      'threads': "3 of the holding changes in Proseware Advisors' latest 13F.\n"
                 '\n'
                 'Northwind (NWT): reduced to about 41.9 million shares, from about 60.1 million the quarter before.\n'
                 '\n'
                 'Litware (LTW): increased to about 12.5 million shares, from about 8 million.',
      'facebook': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 closed out.\n"
                  '\n'
                  'Northwind (NWT): reduced to about 41.9 million shares, from about 60.1 million the quarter '
                  'before.\n'
                  '\n'
                  'Litware (LTW): increased to about 12.5 million shares, from about 8 million.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from about 4.3 million shares the quarter before.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'linkedin': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 closed out.\n"
                  '\n'
                  'Northwind (NWT): reduced to about 41.9 million shares, from about 60.1 million the quarter '
                  'before.\n'
                  '\n'
                  'Litware (LTW): increased to about 12.5 million shares, from about 8 million.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from about 4.3 million shares the quarter before.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'instagram': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 closed "
                   'out.\n'
                   '\n'
                   'Northwind (NWT): reduced to about 41.9 million shares, from about 60.1 million the quarter '
                   'before.\n'
                   '\n'
                   'Litware (LTW): increased to about 12.5 million shares, from about 8 million.\n'
                   '\n'
                   'Woodgrove Bank (WGB): closed out, from about 4.3 million shares the quarter before.\n'
                   '\n'
                   'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                   '\n'
                   "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                   'show when shares changed hands.\n'
                   '\n'
                   'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 closed out.\n"
                '\n'
                'Northwind (NWT): reduced to about 41.9 million shares, from about 60.1 million the quarter before.',
      'youtube_title': "Proseware Advisors' latest 13F: 3 of its holding changes",
      'youtube_description': "3 of the holding changes in Proseware Advisors' latest 13F: 1 reduced, 1 increased, 1 "
                             'closed out.\n'
                             '\n'
                             'Northwind (NWT): reduced to about 41.9 million shares, from about 60.1 million the '
                             'quarter before.\n'
                             '\n'
                             'Litware (LTW): increased to about 12.5 million shares, from about 8 million.\n'
                             '\n'
                             'Woodgrove Bank (WGB): closed out, from about 4.3 million shares the quarter before.\n'
                             '\n'
                             'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                             '\n'
                             "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it "
                             'does not show when shares changed hands.\n'
                             '\n'
                             'What do you look at first in a 13F: new holdings, or changes in share counts?'},
    'f13s_list_listed': {'hook': "Fabrikam Holdings' 13F: 1 reduced, 1 new, 1 closed out.",
      'video_script': ['Compared with the quarter before, it reduced its Northwind holding.',
                       'Compared with the quarter before, it also reported a new Adventure Works holding.',
                       'Compared with the quarter before, it also closed out its Woodgrove Bank holding.',
                       "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later."],
      'cards': [{'title': 'Reduced', 'body': 'Northwind'},
                {'title': 'New holding', 'body': 'Adventure Works'},
                {'title': 'Closed out', 'body': 'Woodgrove Bank'},
                {'title': 'About 13F filings', 'body': 'U.S. stock holdings, due about 45 days after the quarter'}],
      'opening_card': {'kicker': '13F FILING',
                       'logos': ['FBKM'],
                       'headline': "Fabrikam Holdings' latest 13F",
                       'list': {'heading': 'Change in shares',
                                'rows': [{'logo': 'NWT',
                                          'chip': 'NWT',
                                          'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                         {'logo': 'ADVW',
                                          'chip': 'ADVW',
                                          'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                         {'logo': 'WGB',
                                          'chip': 'WGB',
                                          'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']}]},
                       'chip': 'FBKM'},
      'image_spec': {'layout': 'rows',
                     'version': 1,
                     'kicker': '13F FILING',
                     'title': "Fabrikam Holdings' latest 13F",
                     'sections': [{'heading': 'Change in shares',
                                   'rows': [{'logo': 'NWT',
                                             'chip': 'NWT',
                                             'cells': ['Northwind', 'Reduced to 41.9M shares', '−18.1M']},
                                            {'logo': 'ADVW',
                                             'chip': 'ADVW',
                                             'cells': ['Adventure Works', 'New holding', '+5.6M']},
                                            {'logo': 'WGB',
                                             'chip': 'WGB',
                                             'cells': ['Woodgrove Bank', 'Closed out', '−4.3M']}]}],
                     'header': {'logo': 'FBKM', 'name': 'Fabrikam Holdings', 'chip': 'FBKM'},
                     'footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026'},
      'image_post': {'title': "Fabrikam Holdings' latest 13F",
                     'paragraphs': ["It lists the filing's 3 holding changes from the quarter before: 1 reduced, 1 "
                                    'new, 1 closed out.',
                                    'Northwind, reduced to 41.9M shares from 60M; Adventure Works, a new holding of '
                                    '5.6M shares; Woodgrove Bank, closed out, from 4.3M shares.',
                                    'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, 2026.']},
      'persons': [],
      'image_footer': 'Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026',
      'logo_refs': [{'key': 'FBKM', 'name': 'Fabrikam Holdings'},
                    {'key': 'NWT', 'name': 'Northwind'},
                    {'key': 'ADVW', 'name': 'Adventure Works'},
                    {'key': 'WGB', 'name': 'Woodgrove Bank'}],
      'source_ref': 'news:thirteen_f_spotlight:0009999902:2026-Q3:NWT',
      'x': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.",
      'bluesky': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.",
      'threads': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.\n"
                 '\n'
                 'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                 '\n'
                 'Adventure Works (ADVW): a new holding of 5.6 million shares.',
      'facebook': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.\n"
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'linkedin': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.\n"
                  '\n'
                  'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                  '\n'
                  'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                  '\n'
                  'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                  '\n'
                  'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                  '\n'
                  'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                  '\n'
                  "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                  'show when shares changed hands.\n'
                  '\n'
                  'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'instagram': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.\n"
                   '\n'
                   'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                   '\n'
                   'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                   '\n'
                   'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                   '\n'
                   'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                   '\n'
                   'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                   '\n'
                   "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it does not "
                   'show when shares changed hands.\n'
                   '\n'
                   'What do you look at first in a 13F: new holdings, or changes in share counts?',
      'tiktok': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.\n"
                '\n'
                'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.',
      'youtube_title': "Fabrikam Holdings' latest 13F: 3 holding changes",
      'youtube_description': "Fabrikam Holdings' latest 13F: 1 holding reduced, 1 new, 1 closed out.\n"
                             '\n'
                             'Northwind (NWT): reduced to 41.9 million shares, from 60 million the quarter before.\n'
                             '\n'
                             'Adventure Works (ADVW): a new holding of 5.6 million shares.\n'
                             '\n'
                             'Woodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n'
                             '\n'
                             'Adventure Works first appears in this filing; it was listed in August 2026.\n'
                             '\n'
                             'The filing covers the quarter that ended Sep 30 and was filed on Nov 13.\n'
                             '\n'
                             "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; it "
                             'does not show when shares changed hands.\n'
                             '\n'
                             'What do you look at first in a 13F: new holdings, or changes in share counts?'}
}


#: The caption fields a golden may pin (each read from `captions`).
_GOLDEN_CAPTION_KEYS = ("x", "bluesky", "threads", "facebook", "linkedin", "instagram", "tiktok", "youtube_title",
                        "youtube_description")


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_golden_packages(name, factory, run_date):
    out = compose(factory(), run_date)
    golden = {**GOLDEN, **GOLDEN_2B, **GOLDEN_SPOTLIGHT}[name]
    for key, value in golden.items():
        got = out["captions"][key] if key in _GOLDEN_CAPTION_KEYS else out[key]
        assert got == value, f"{name}.{key}"


def test_every_sample_has_exactly_one_golden():
    tables = (set(GOLDEN), set(GOLDEN_2B), set(GOLDEN_SPOTLIGHT))
    assert set().union(*tables) == {s[0] for s in SAMPLES}
    assert sum(len(t) for t in tables) == len(SAMPLES)          # no sample in two tables


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_every_sample_composes_a_valid_deterministic_json_package(name, factory, run_date):
    a, b = compose(factory(), run_date), compose(factory(), run_date)
    assert a == b
    assert json.loads(json.dumps(a, allow_nan=False)) == a
    assert T.validate_package(a, record=factory()) == []
    assert T.validate_package(a) == []          # without the record: stricter, still clean
    # news-v22: the first-line prefix switch off (the default) — no prefix, the switch frozen False
    assert a["first_line_prefix_on"] is False and a["first_line_prefix"] is None
    # ... and on: deterministic, clean with the record; WITHOUT it, a BREAKING prefix is exactly one violation (its
    # freshness cannot be shown — stricter, never looser), every other prefix (or none) still clean
    on = T.compose(factory(), run_date=run_date, store_state="live", allow_x_url=False, first_line_prefix=True)
    assert on == T.compose(factory(), run_date=run_date, store_state="live", allow_x_url=False,
                           first_line_prefix=True)
    assert json.loads(json.dumps(on, allow_nan=False)) == on and on["first_line_prefix_on"] is True
    assert T.validate_package(on, record=factory()) == []
    breaking = on["first_line_prefix"] in {T.FIRST_LINE_PREFIXES[i] for i in T.BREAKING_PREFIX_IDS}
    assert T.validate_package(on) == ([{"field": "first_line_prefix", "code": "structure",
                                        "detail": "BREAKING needs its record: its freshness cannot be shown"}]
                                      if breaking else [])
    assert a["authorship"] == "template" and a["video_layout"] == "per_line"
    assert a["template_version"] == T.TEMPLATE_VERSION == "news-v23"
    assert a["content_class"] == R.SERIES_CLASS[a["series"]] == selection.SERIES_BY_ID[a["series"]].content_class
    assert a["source_ref"] == R.ledger_key(factory())
    assert a["carousel_slides"] == [] and a["dropped_outlets"] == {}
    assert sorted(a["posts"]) == sorted(post_copy.PLATFORMS)
    assert a["disclaimer_card"] == post_copy.disclaimer_card(run_date, "template")


# ── the lexicon ───────────────────────────────────────────────────────────────


def test_the_lexicon_is_pinned_verbatim_and_versioned():
    doc = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    # news-v22: the closed first-line prefixes are pinned beside the lexicon, under their own key (kept out of
    # LEXICON, so the lexicon's word scan needs no exemption for "BREAKING" / "alert" / an emoji)
    assert set(doc) == {"_about", "version", "entries", "first_line_prefixes"}
    assert doc["version"] == T.TEMPLATE_VERSION == "news-v23"
    assert doc["entries"] == T.LEXICON
    assert list(doc["entries"]) == list(T.LEXICON)
    assert doc["first_line_prefixes"] == T.FIRST_LINE_PREFIXES
    assert list(doc["first_line_prefixes"]) == list(T.FIRST_LINE_PREFIXES)
    assert not set(T.FIRST_LINE_PREFIXES) & set(T.LEXICON)


#: A neutral value for every slot the lexicon uses (a test fails on a slot without one).
NEUTRAL = dict(
    a="Northwind", b="Fabrikam", c="Contoso", k=2, m=2, n=3, source="SEC Form 4 filings",
    as_of="Filed Nov 9–15, 2026", incl="2 reduced and 1 new", co="Northwind", co_s="Northwind's", co_sp="Northwind", co_sp_s="Northwind's",
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
    # news-v12: a late Form 4 row's trade dates; a stake cover's chip
    first_trade_sp="October 17th", last_trade_sp="October 24th", first_trade_cap="Oct 17", last_trade_cap="Oct 24",
    trade_window_img="Oct 17–24, 2026", basis_title="Committed up to",
    # news-v16: the theme image's fiscal years
    fys="2025 or 2026",
    # news-v19 (Batch 1, plan item 5): a stake's investee as a noun phrase (a company's own name: no article)
    ee_np="Fabrikam", ee_np_cap="Fabrikam",
    # news-v22 (2026-11-15): the spotlight lane — a ticker in brackets, the filing's own cost per share, the lead's
    # filing days, a fourth list item, a revenue line's share, a 13F holding's share counts and amendment day
    sym="NWND", cost_s="$142.10", cost_min_s="$142.10", cost_max_s="$143.85", first_filed_cap="Nov 10",
    last_filed_cap="Nov 12", d="Litware", sh="54%", prev_w="60 million", prev_s="60M", prev_noun="shares",
    amended_cap="Nov 20", shares_n="5.6M",
    # owner feedback 2026-11-19: the 13F spotlight's big figure is the CHANGE in shares ("−18.1M shares")
    diff_s="18.1M",
    # owner feedback 2026-11-19: the 13F spotlight LIST — where its changes are, the hook's verb clauses, "new ones"
    where="Northwind Capital's latest 13F", clauses="reduced 2 holdings and increased 1", ones="ones",
    # news-v23 (restyle 2026-11-19): the date a short picture footer ends with ("filed {date_img}")
    date_img="Nov 13, 2026",
    # owner decisions 2026-11-20 (the insider list): who disclosed buying, by role (a caption's / the narration's)
    who="the CEO", who_sp="its chief executive",
    # news-v23 (the restyle, owner 2026-11-19): ONE post of several companies — a Congress Count's other companies and
    # their counts, an earnings post's report days and a surprise figure ("+{p}%")
    n2=2, n3=2, co2="Fabrikam", co3="Contoso", co2_sp="Fabrikam", day_cap="Nov 5", first_cap="Nov 3",
    last_cap="Nov 5", p="8.0",
    # news-v23 (company stakes, owner 2026-11-19): the as-of span of a post of several stakes (its alt text)
    first="Dec 1, 2025", last="Mar 2, 2026",
    # news-v23 phase 3 (the weekly theme post): the sub line's count, the market-value angle's companies, values, day
    # and rank
    count="14 companies", co1="Northwind", v1="$171 billion", v2="$152 billion", v3="$134 billion",
    v1_img="$171B", v2_img="$152B", v3_img="$134B", day_sp="November 24th", date_cap="Nov 24", rank=1,
    v="$171 billion",
    # news-v23 phase 3, the last three angles (2026-11-20): a growth figure and its words, a profit per $100 in words,
    # an insider post's second amount, its sales and the theme's unchecked companies
    g="34", g_img="+34%", growth="34% higher than the year before", profit="$40 of profit per $100 of revenue",
    s="$8.6 million", amt2="$1.2M", u=2,
    # owner decision 2026-11-20 (the Money Map loss year's costs card): the costs figure, the other income's figure and
    # the costs per $100 of revenue
    costs_s="$9.7B", other_s="$110M", c100="$166.38",
)
#: The 13F subjects are the filer's latest 13F, not a person (their own neutral fills; the 13F spotlight's subject is
#: the filer itself since the owner's feedback of 2026-11-19 — "{filer} reduced its {co} holding" — so it needs none).
NEUTRAL_13F = dict(NEUTRAL, subj="Northwind Capital's latest 13F", subj_sp="Northwind Capital's latest 13F")


def neutral(entry_id):
    fills = NEUTRAL_13F if entry_id.startswith(("f13.", "f13s.")) else NEUTRAL
    return T.LEXICON[entry_id].format_map(fills)


def slots_of(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_slot_has_a_neutral_fill_and_every_entry_renders():
    used = set().union(*(slots_of(v) for v in T.LEXICON.values()))
    assert used <= set(NEUTRAL_13F), sorted(used - set(NEUTRAL_13F))
    for entry_id in T.LEXICON:
        assert clean(neutral(entry_id)) == neutral(entry_id), entry_id


#: news-v23 (the earnings table, owner 2026-11-19: "add +X% surprised (in green) or -X% surprised (in red)" + the column
#: order "Estimate, Reported, Surprise"): the surprise cells — the one other "%" that stands alone — and the column
#: label "Surprise" — the one string EARNINGS_BANNED_RE's `surpris\w*` lets stand. Both exemptions are POSITIONAL in the
#: validator (`news_templates._SURPRISE_CELL_FIELD_RE` / `_SURPRISE_COLUMN_FIELD_RE`), each with must-reject twins below
#: (`test_news_v23_the_surprise_exemptions_are_positional_*`).
SURPRISE_CELL_IDS = frozenset({"er.img.sur.plus", "er.img.sur.minus", "er.img.sur.zero", "er.img.sur.none"})
SURPRISE_COLUMN_ID = "er.img.col.surprise"
#: news-v23 phase 3 (2026-11-20): the growth + profit angle's entries that carry a revenue growth "%" — exempt only in the
#: phrase `news_templates._GROWTH_PHRASE_RE` names (positional: "revenue" right before the figure), with must-reject
#: twins in `test_phase3_the_growth_percent_exemption_is_positional_with_must_reject_twins`.
GROWTH_PHRASE_IDS = frozenset({"th.gp.up", "th.gp.down", "th.gp.l", "th.gp.l.short", "th.gp.l.min", "th.gp.c.body",
                               "th.gp.c.body.min", "th.gp.p.row", "th.gp.p.short"})


@pytest.mark.parametrize("entry_id", sorted(T.LEXICON))
def test_every_entry_passes_the_banned_forecast_news_and_structure_rules(entry_id):
    text = neutral(entry_id)
    assert not copy_rules.contains_banned_copy(text), entry_id
    assert not copy_rules.contains_forecast(text), entry_id
    assert not T.NEWS_BANNED_RE.search(text), entry_id
    assert not T.OFF_TABLE_VERBS_RE.search(text), entry_id
    assert "!" not in text and "#" not in text and "@" not in text
    # the "%" that stands alone: a grid tile's share cell ("Retail · 57%", templates design §4.5) and — news-v23, the
    # earnings table (owner 2026-11-19) — the surprise cell, in its own shapes only
    assert T._percent_ok(text) or (entry_id == "th.img.line.share" and T._SHARE_CELL_RE.fullmatch(text)) or (
        entry_id in SURPRISE_CELL_IDS and T._SURPRISE_CELL_RE.fullmatch(text)) or (
        # news-v23 phase 3: the growth + profit angle's one phrase ("revenue … % higher / lower than the year before",
        # "revenue +34%,") — the two bare growth words are always written after "revenue" (`th.gp.l` …)
        entry_id in GROWTH_PHRASE_IDS and T._percent_ok(T._GROWTH_PHRASE_RE.sub(
            "revenue", f"revenue {text}" if entry_id in ("th.gp.up", "th.gp.down") else text))), entry_id
    assert not T._CASHTAG_RE.search(text), entry_id
    assert R.congress_name_hits(text) == [], entry_id                     # the Congress roster scan
    # the 2b series' own word rules hold on every entry of the series
    family = entry_id.split(".")[0]
    if family == "cg":
        assert not T.CONGRESS_NARROWING_RE.search(text) and not T.CONGRESS_OFF_VERBS_RE.search(text), entry_id
        assert "$" not in text, entry_id
    if family == "er" and entry_id != SURPRISE_COLUMN_ID:   # the column LABEL's own positional exemption (news-v23)
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
    "ins.l3.indirect.all",
    "ins.c1.noname.officer", "ins.c3.indirect.all.body",
    "ins.ch.one.long.one", "ins.ch.one.long.many", "ins.ch.one.short", "ins.ch.one.min",
    "ins.p.rows", "ins.p.rows2", "ins.row.name", "ins.row.noname",
    "ins.p.one.name.one", "ins.p.one.name.many", "ins.p.one.noname.one", "ins.p.one.noname.many",
    "ins.yt.long", "ins.yt.short", "ins.yt.min", "ins.alt.row", "ins.alt.rows",
    # news-v15: the count-neutral twins of the ".one" entries above (the same role / person slots)
    "ins.l1.role.neutral", "ins.l1.role.short.neutral", "ins.ch.one.long.neutral",
    "ins.p.one.name.neutral", "ins.p.one.noname.neutral",
    # owner decisions 2026-11-20: the insider LIST's role descriptors — each row's "CEO + at least 2 other insiders"
    # (picture, caption, narration), the lead's role in the narration, a row paragraph (a role, and a name only in that
    # body paragraph), the rest's short paragraph and the alt text's rows — the same design as the rows above
    "isp.who.named", "isp.who.label.plus.one", "isp.who.label.plus.many", "isp.who.np.plus.one",
    "isp.who.np.plus.many", "isp.who.sp.plus.one", "isp.who.sp.plus.many", "isp.sp.ceo", "isp.sp.cfo",
    "isp.l1", "isp.c1.body", "isp.p.row.one", "isp.p.row.range", "isp.p.more", "isp.alt.rows", "isp.alt.row",
})
#: `class_b_valuation` only on the stake's FAIR-VALUE entries (contract D7): the investor's own
#: accounting measure of its stake, as the source reports it — never Caydex's value of a stock.
#: news-v17: the fair-value headlines are the possessive form and its ".of" twin (no "reported its").
CLASS_B_VALUATION_IDS = frozenset({"st.h.fair_value.poss", "st.h.fair_value.of",
                                   "st.l2.fair_value", "st.label.fair_value",
                                   "st.c2.fair_value.title",
                                   # news-v23 (one investor's stakes): the same measure, in the list's own lines
                                   "st.m.l.basis.fair_value", "st.m.o.fair_value", "st.m.p.short.fair_value",
                                   # news-v23 phase 3, the weekly theme post's "biggest" angle: a company's MARKET
                                   # VALUE, a dated fact (owner 2026-09-28, marketing.md §1: "valuation figures
                                   # calculated from a price — market cap …" are allowed on a public post) — never an
                                   # opinion of value (THEME_BANNED_RE refuses "worth", "valuation", "biggest", "top")
                                   "th.big.sub", "th.big.hook", "th.big.hook.part", "th.big.hook.min", "th.big.l1",
                                   "th.big.l1.short", "th.big.l3", "th.big.c1.title", "th.big.c3.title",
                                   "th.big.ch.long", "th.big.ch.long.part", "th.big.ch.short", "th.big.ch.short.part",
                                   "th.big.p.explainer", "th.big.yt", "th.big.yt.part", "th.big.alt.body",
                                   "th.big.alt.body.all", "th.big.alt.body.some"})
#: `brand_mention` only on the theme's grouping lines: the grouping IS Caydex's, and says so.
BRAND_MENTION_IDS = frozenset({"th.grouping", "th.c.grouping.title"})
#: `source_reference` only on the stake lines that point at the filing the post NAMES (its "Source: …"
#: line): "from the source named in this post", "the source says up to that amount". The rule (2026-10-31)
#: is for MODEL text citing its own brief ("The fact sheet shows…"); these code-owned lines cite a real,
#: named document on purpose and are accurate.
SOURCE_REFERENCE_IDS = frozenset({"st.l1", "st.l2.committed_up_to", "st.l2.carrying_value",
                                  "st.l2.fair_value", "st.l2.invested",
                                  # news-v23: a list of several stakes — each figure's source is named beside it
                                  "st.m.l.basis.mixed"})


def test_every_exempted_id_is_a_live_entry():
    """news-v19: `ins.open.head.min` left the lexicon with the cover's second company name; an exemption for
    an id that no longer exists would silently stand ready for a new entry of that name."""
    assert (PERSON_NAMED_IDS | CLASS_B_VALUATION_IDS | BRAND_MENTION_IDS
            | SOURCE_REFERENCE_IDS) <= set(T.LEXICON)


@pytest.mark.parametrize("entry_id", sorted(T.LEXICON))
def test_the_public_copy_scan_finds_only_the_exempted_codes(entry_id):
    codes = {v.code for v in scan_text(entry_id, neutral(entry_id))}
    allowed = ({"person_named"} if entry_id in PERSON_NAMED_IDS else set()) | (
        {"class_b_valuation"} if entry_id in CLASS_B_VALUATION_IDS else set()) | (
        {"brand_mention"} if entry_id in BRAND_MENTION_IDS else set()) | (
        {"source_reference"} if entry_id in SOURCE_REFERENCE_IDS else set())
    assert codes <= allowed, (entry_id, codes)


def test_the_2b_exemption_tables_are_exact():
    """Every exempted 2b id really draws its code (no stale exemption that would hide a new hit).
    Mutation-checked by hand (2026-10-10, in memory): "worth" in `er.l2.revenue` turned the public-copy
    scan red; "a bargain" in `st.l4` turned the banned-word test red (scan_text has no row for it)."""
    for ids, code in ((CLASS_B_VALUATION_IDS, "class_b_valuation"), (BRAND_MENTION_IDS, "brand_mention"),
                      (SOURCE_REFERENCE_IDS, "source_reference")):
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
        if factory().series not in T.FORM4_PERSON_SERIES:          # news-v22: the insider spotlight too
            continue
        out = compose(factory(), run_date)
        assert not re.search(r"open[\s-]*market", json.dumps(out), re.IGNORECASE), name
        if factory().series == "insider_spotlight":
            # owner decisions 2026-11-20: the insider LIST narrates its companies' "disclosed purchases"
            assert "disclosed purchases" in out["video_script"][1], name
        else:
            assert re.search(r"\b(?:one purchase|\d+ purchases)\b", out["video_script"][1]), name
        assert "disclosed buying" in out["hook"], name


# ── the shipped series ────────────────────────────────────────────────────────


#: Contract D9 "Layout per series".
SERIES_LAYOUT = {"ceo_buys": "rows", "insider_buys": "rows", "thirteen_f": "rows", "congress_count": "rows",
                 # news-v23: one investor's stakes in public companies — a `rows` list (stakes_nvidia.jpg)
                 "company_stakes": "rows", "earnings": "rows", "money_map": "bars", "theme_explainer": "grid",
                 # news-v23: the 13F spotlight is the ONE 13F list (`rows`), as the roundup; owner decisions 2026-11-20:
                 # the insider spotlight is the insider LIST (`rows`), no longer the one-issuer art-band picture
                 "insider_spotlight": "rows", "thirteen_f_spotlight": "rows"}
#: news-v23 phase 3: a theme post draws its ANGLE's layout — "how they make money" the grid, "who's biggest" the
#: ranked list (theme_biggest_power.jpg).
THEME_ANGLE_LAYOUT = {"make_money": "grid", "biggest": "ranked", "growth_profit": "figures", "insiders": "ledger",
                      "congress": "tally"}


def layout_of(rec):
    """The layout a record's picture draws (its angle's, for a theme)."""
    if rec.series == "theme_explainer":
        return THEME_ANGLE_LAYOUT[rec.angle]
    return SERIES_LAYOUT[rec.series]


def footer_source_of(rec):
    """The source words a record's picture footer names (a theme's: its angle layout's)."""
    if rec.series == "theme_explainer":
        return T.THEME_FOOTER_SOURCE_OF_LAYOUT[layout_of(rec)]
    return T.FOOTER_SOURCE_OF_SERIES[rec.series]


def test_series_specs_cover_every_series_and_every_shipped_one():
    """Every series is composed in code (drop 2b ships the four 2b templates); a run reaches one only
    once it is in SHIPPED_SERIES (and the per-series switch), so the shipped set is a subset — and
    a shipped series' layout is shipped (`test_marketing_layouts_2b` pins both sides of that)."""
    # news-v22 (2026-11-15): the spotlight pair shipped with its templates — the strict pin of before is back (the
    # lane work relaxed it while the registry and the records named the pair ahead of its templates)
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
    (-4_300_000_000, "−$4.3B", "-$4.3 billion"),          # the IMAGE form's sign is U+2212 (every drawn figure's)
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
    (2026-10-09): without the "more than 99%" guard in `pct` this turns red. news-v19: the near-whole line is
    "Hardware" (the adapter now folds a generic "Products" into the rest) and L2 says no "Its … business"."""
    rec = costco(segments=(R.Segment("Hardware", 274_100_000_000.0), R.Segment("Licensing", 1_100_000_000.0)))
    out = compose(rec, THU)
    for text in [out["video_script"][1], out["cards"][1]["body"]]:
        assert "100% of" not in text, text
        assert "more than 99% of revenue" in text and "less than 1% of revenue" in text, text
    assert out["video_script"][1] == ("Hardware made up more than 99% of revenue, and Licensing made up "
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
    # owner decisions 2026-11-20: the cover is the roundup's list; the lead row's ".of" subject leads its video captions
    assert out["captions"]["tiktok"].startswith("The CEO of Lowe's disclosed buying $2.3 million of the company's stock")
    assert "Lowe's's" not in json.dumps(out) and "Lowe's'" not in json.dumps(out)
    # news-v23: the 13F list's ".of" twins — the title, the cover's headline, the hook and the captions
    f13 = compose(berkshire(filer_name="Moody's"), TUE)
    assert f13["hook"] == "5 of the holding changes by Moody's: 3 new, 2 closed out."
    assert f13["image_spec"]["title"] == f13["opening_card"]["headline"] == "The latest 13F of Moody's"
    assert f13["captions"]["x"].startswith("5 of the holding changes in the latest 13F of Moody's")
    assert f13["captions"]["youtube_title"] == "The latest 13F of Moody's: 5 of its holding changes"
    fisher = compose(berkshire(filer_name="Fisher Investments"), TUE)
    assert fisher["image_spec"]["title"] == "Fisher Investments' latest 13F"
    assert fisher["hook"].startswith("5 of the holding changes in Fisher Investments' ")


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
    assert out["captions"]["youtube_title"].startswith("NVIDIA's CEO")


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


@pytest.mark.parametrize("holding, amended, l3", [
    ("direct", False, "ins.l3.explainer"),
    ("indirect", False, "ins.l3.indirect.all"),
    ("mixed", False, "ins.l3.indirect.some"),
    ("direct", True, "ins.l3.amended"),
    ("indirect", True, "ins.l3.indirect.all"),
    ("mixed", True, "ins.l3.indirect.some"),
])
def test_form4_lead_line_three_and_the_pictures_notes(holding, amended, l3):
    """The LEAD row's narration line 3 (its holding, its amendment or the explainer) and the picture's notes about the
    rows it draws. Owner decisions 2026-11-20 ("3 - 5 companies at once"): line 4 is always the other rows' "At least
    {m} more …" — the one-company post's own line 4 and its `spotlight` picture are gone."""
    row = buy("GME", "GameStop", "ceo", None, 500_000.0, 20_000.0, 1, [date(2026, 11, 10)], holding=holding,
              amended=amended)
    out = compose(week(row), MON)
    assert out["video_script"][2] == T.LEXICON[l3].format(role_sp="chief executive")
    assert out["video_script"][3].startswith("At least 2 more chief executives")
    assert out["image_spec"]["layout"] == "rows"
    notes = " ".join(out["image_spec"].get("notes", []))
    assert ("indirectly" in notes) == (holding != "direct")
    assert ("Form 4/A" in notes) == amended


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
    (("indirect", "indirect", "indirect"), "The shares in these purchases are reported as held indirectly."),
    (("indirect", "mixed", "indirect"), "Some of the shares in these purchases are reported as held indirectly."),
    (("direct", "indirect", "direct"), "Some of the shares in these purchases are reported as held indirectly."),
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
        other = ("Some of the shares in these purchases are reported as held indirectly." if note.startswith("The ")
                 else "The shares in these purchases are reported as held indirectly.")
        assert other not in alt and other not in notes
    else:
        assert "indirectly" not in alt


def test_form4_roles_subjects_and_plurals():
    cfo = buy("NKE", "Nike", "cfo", None, 1_000_000.0, 10_000.0, 1, [date(2026, 11, 10)])
    director = buy("HSY", "Hershey", "director", None, 300_000.0, 1_500.0, 1, [date(2026, 11, 11)])
    out = compose(week(cfo, series="insider_buys"), MON)
    assert out["hook"] == "Nike's chief financial officer disclosed buying $1 million of the company's stock."
    assert out["captions"]["tiktok"].startswith("Nike's CFO disclosed buying $1 million of the company's stock")
    out = compose(week(director, series="insider_buys"), MON)
    assert out["hook"] == "A director of Hershey disclosed buying $300,000 of the company's stock."
    assert out["cards"][0] == {"title": "Who filed", "body": "A director of Hershey"}
    # owner decisions 2026-11-20: a week is three companies or more (the WEEK_FILLERS: a director and a CFO)
    out = compose(week(cfo, director, series="insider_buys"), MON)
    assert out["image_spec"]["title"] == "At least 3 company insiders disclosed buying their own company's stock"
    assert out["video_script"][3] == ("At least 2 more directors disclosed buying their own company's stock in the "
                                      "same week.")
    dirs = [director, replace(director, company=co("CTSO", "Contoso"), amount_usd=200_000.0),
            replace(director, company=co("LTW", "Litware"), amount_usd=150_000.0)]
    three_dirs = compose(week(*dirs, series="insider_buys"), MON)
    assert three_dirs["image_spec"]["title"] == "At least 3 directors disclosed buying their own company's stock"
    assert three_dirs["video_script"][3] == ("At least 2 more directors disclosed buying their own company's stock in "
                                             "the same week.")


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
    # news-v19 (Batch 1): the subject names the company, so the headline says "of the company's stock" (owner decisions
    # 2026-11-20: the lead row's headline leads the VIDEO captions; the picture's are the roundup's)
    first = out["captions"]["tiktok"].split("\n")[0]
    assert first.startswith(f"A director of {name} disclosed buying $1 million of the company's stock")
    assert first.count(name) == 1
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


# ── 13F: the ONE list design (news-v23) — see the section at the end of this file ───────────────────


# ── Money Map variants ────────────────────────────────────────────────────────


def test_money_map_loss_year_reads_as_losses_and_outlines_its_bars():
    out = compose(rivian(), THU)
    assert out["hook"] == "Rivian Automotive had a net loss of $66.38 for every $100 of revenue."
    assert out["video_script"][3] == "After interest, taxes and everything else, the net loss was $3.9 billion."
    flow = out["image_spec"]["flow"]
    assert [b["label"] for b in flow] == ["Revenue", "Gross loss", "Operating loss", "Net loss"]
    assert [b["style"] for b in flow] == ["fill", "outline", "outline", "outline"]
    # news-v23 (2026-11-20): the cover reads like the picture — its headline, then the figure with its label under it
    assert out["opening_card"]["headline"] == "How Rivian Automotive makes money"
    assert (out["opening_card"]["figure"], out["opening_card"]["figure_label"]) == (
        "$66.38", "net loss for every $100 of revenue")
    assert out["image_spec"]["callout"] == "For every $100 of revenue, the net loss was $66.38"


def test_money_map_eliminations_change_the_basis_and_draw_an_outline_bar():
    out = compose(comcast(), THU)
    assert "of combined sales" in out["video_script"][1]
    bars = out["image_spec"]["segments"]
    assert bars[-2]["label"] == "Everything else" and bars[-1] == {
        "label": "Intersegment eliminations", "value": "−$4.3B", "ratio": 0.0524, "style": "outline"}
    assert out["video_script"][2] == "After all operating costs, operating profit was $23.3 billion that year."
    assert all(0 <= b["ratio"] <= 1 for b in bars + out["image_spec"]["flow"])
    assert "Sales between parts of the company, $4.3 billion, are taken out of that total." in \
        out["captions"]["linkedin"]
    # eliminations None (the adapter publishes none it cannot verify): no string claims any (news-v19: the
    # sentence says "parts of the company" — a region map's parts are no business lines)
    plain = json.dumps(compose(costco(), THU)).lower()
    assert "elimination" not in plain and "between parts of the company" not in plain
    assert "business line" not in json.dumps(out).lower() and "business line" not in plain


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


# ── news-v19: Money Map wording (Batch 1, plan item 2) ────────────────────────

_MM_SEG_PARA = re.compile(r"How fiscal \d{4} revenue broke down( by region)?: ")


def _seg_para(out):
    (para,) = [p for p in out["captions"]["linkedin"].split("\n\n") if _MM_SEG_PARA.match(p)]
    return para


def _regions_with_rest(rest_usd):
    """All-regional parts summing to $270B and a rest of ``rest_usd``: at $30B the rest is EXACTLY 10% of revenue."""
    return costco(segments=(R.Segment("United States", 180_000_000_000.0), R.Segment("Canada", 45_000_000_000.0),
                            R.Segment("Other International", 45_000_000_000.0)),
                  other_usd=rest_usd, revenue_usd=270_000_000_000.0 + rest_usd)


@pytest.mark.parametrize("factory, regional", [
    # every named part a region by its exact name, but the rest is 18% of revenue (MM-6: Coca-Cola's live rest is
    # mostly businesses) — the neutral wording
    (coca_cola, False),
    (lambda: coca_cola(other_usd=None, revenue_usd=39_400_000_000.0), True),   # the same regions, no rest
    (costco, True),                     # countries, and Costco's own "Other International"
    (lambda: costco(other_usd=1_000_000_000.0, revenue_usd=276_200_000_000.0), True),   # the rest bar is no part
    (lambda: _regions_with_rest(30_000_000_000.0), True),             # the rest at exactly 10%: still regional
    (lambda: _regions_with_rest(30_000_000_100.0), False),            # a hundred dollars past it: neutral
    (lambda: _regions_with_rest(0.0), True),
    (mcdonalds, False),                 # International Operated Markets: markets McDonald's runs, not one place
    (lambda: costco(segments=(R.Segment("United States", 200_000_000_000.0), R.Segment("Canada", 38_500_000_000.0),
                              R.Segment("Membership Fees", 36_700_000_000.0))), False),    # one business line
    (lambda: costco(segments=(R.Segment("Frito-Lay North America", 200_000_000_000.0),
                              R.Segment("Latin America", 75_200_000_000.0))), False),      # a region word inside
    (rivian, False), (comcast, False), (amazon, False), (lambda: apple(), False),     # apple(): defined below
], ids=["coca_cola_large_rest", "coca_cola_no_rest", "costco", "costco_with_rest", "rest_at_10_percent",
        "rest_past_10_percent", "rest_zero", "mcdonalds", "one_business_line", "region_word_inside",
        "rivian", "comcast", "amazon", "apple"])
def test_money_map_reads_by_region_only_when_every_named_part_is_a_region(factory, regional):
    """Batch 1 (plan item 2): every map said "Revenue by product or business line" — false for McDonald's
    (markets) and Costco (countries). Now the neutral "How fiscal 2025 revenue broke down", and "by region" only
    when EVERY named part is on `R.REGION_NAMES` by its exact name; the alt text follows the same rule.
    Mutation-checked by hand (2026-10-15, in memory): `by_region` forced False turns the regional rows red, forced
    True the neutral rows. The review of 2026-10-16 (MM-6): and only while the rest ("Everything else") is at most
    `T.MONEY_MAP_REGION_MAX_OTHER_SHARE` of revenue — exact at the boundary. Mutation-checked by hand (2026-10-19):
    the share clause dropped turns the two large-rest rows red; `<=` made `<` turns the 10% row red."""
    rec = factory()
    out = compose(rec, THU)
    assert T.validate_package(out, record=rec) == []
    para, alt = _seg_para(out), out["image_post"]["paragraphs"][0]
    if regional:
        assert para.startswith("How fiscal 2025 revenue broke down by region: "), para
        assert alt.startswith("Bars show revenue by region: "), alt
    else:
        assert para.startswith("How fiscal 2025 revenue broke down: "), para
        assert alt.startswith("Bars show how revenue broke down: "), alt
    text = json.dumps(out)
    assert ("by region" in text) is regional
    assert "business line" not in text and "Its " not in " ".join(out["video_script"])


def test_a_region_takes_the_in_running_text_and_never_on_a_label():
    """"the United States" in the narration and the caption list ("The …" opening a sentence), never on what
    is drawn or labelled: the bars, the cards, the cover and the alt text keep the name as the company writes
    it. Mutation-checked by hand (2026-10-15, in memory): with `said()` returning the bare name the narration
    asserts turn red; with the article on the bar labels the label asserts do."""
    out = compose(mcdonalds(), THU)
    assert out["video_script"][1] == ("International Operated Markets made up 51% of revenue, and the United States "
                                      "made up 40% of revenue.")
    assert "; the United States, $10.8 billion; " in _seg_para(out)
    assert [b["label"] for b in out["image_spec"]["segments"]] == [
        "International Operated Markets", "United States", "Everything else"]
    assert out["cards"][1]["body"] == "International Operated Markets: 51% of revenue · United States: 40% of revenue"
    drawn = json.dumps([out["image_spec"], out["cards"], out["opening_card"], out["image_post"]])
    assert "the United States" not in drawn.replace("The United States", "") and "The United States" not in drawn
    for s in onscreen.image_strings(out["image_spec"], out["logo_refs"]):
        assert not s.lower().startswith("the united states"), s
    # the sentence-opening form
    lead = compose(costco(), THU)
    assert lead["video_script"][1].startswith("The United States made up 73% of revenue, and Canada made up ")
    assert "by region: the United States, $200 billion; Canada, $38.5 billion;" in _seg_para(lead)
    # "the Americas" too; a region with no article ("Canada", "Asia Pacific") stays bare
    americas = compose(costco(segments=(R.Segment("Americas", 150_000_000_000.0), R.Segment("Asia Pacific",
                                                                                            125_200_000_000.0))), THU)
    assert americas["video_script"][1].startswith("The Americas made up 55% of revenue, and Asia Pacific made up ")
    assert _seg_para(americas).startswith("How fiscal 2025 revenue broke down by region: the Americas, $150 billion; "
                                          "Asia Pacific, $125.2 billion.")
    assert T.validate_package(out, record=mcdonalds()) == [] and T.validate_package(americas) == []
    # the one-part line opens on the article too: a "combined sales" map whose two-part line runs to 21 words
    rec = costco(segments=(R.Segment("United States", 200_000_000_000.0),
                           R.Segment("Europe, Middle East and Africa", 42_400_000_000.0),
                           R.Segment("Other International", 38_500_000_000.0)), eliminations_usd=-5_700_000_000.0)
    one = compose(rec, THU)
    assert one["video_script"][1] == "The United States made up 71% of combined sales that year."
    assert _seg_para(one).startswith("How fiscal 2025 revenue broke down by region: the United States, $200 billion; ")
    assert _seg_para(one).endswith("Sales between parts of the company, $5.7 billion, are taken out of that total.")
    assert T.validate_package(one, record=rec) == []


def _two_parts(first, second, *, first_usd=150_000_000_000.0, second_usd=125_200_000_000.0):
    return costco(segments=(R.Segment(first, first_usd), R.Segment(second, second_usd)))


@pytest.mark.parametrize("first, second, line, item", [
    # the review of 2026-10-16 (MM-3): since Batch 1 the name OPENS narration line 2, so a name that needs "the"
    # must get it — "U.S. made up 50% of revenue" was narrated and burned into the captions
    ("U.S.", "International", "The U.S. made up 55% of revenue, and International made up 45% of revenue.",
     "by region: the U.S., $150 billion; International, $125.2 billion."),
    # (the two-part line would run to 21 words: the one-part line, as for any long pair)
    ("United States and Canada", "Rest of the World",
     "The United States and Canada made up 55% of revenue that year.",
     "by region: the United States and Canada, $150 billion; the Rest of the World, $125.2 billion."),
    ("Europe", "Rest of World", "Europe made up 55% of revenue, and the Rest of World made up 45% of revenue.",
     "by region: Europe, $150 billion; the Rest of World, $125.2 billion."),
    # MM-2: an acronym region is named (never folded) and reads "the" too
    ("US", "Asia Pacific", "The US made up 55% of revenue, and Asia Pacific made up 45% of revenue.",
     "by region: the US, $150 billion; Asia Pacific, $125.2 billion."),
    ("UK", "EU", "The UK made up 55% of revenue, and the EU made up 45% of revenue.",
     "by region: the UK, $150 billion; the EU, $125.2 billion."),
    ("Greater China", "Rest of Asia Pacific",
     "Greater China made up 55% of revenue, and the Rest of Asia Pacific made up 45% of revenue.",
     "by region: Greater China, $150 billion; the Rest of Asia Pacific, $125.2 billion."),
    # a product or business line is a name: it opens the line as written
    ("iPhone", "Services", "iPhone made up 55% of revenue, and Services made up 45% of revenue.",
     "broke down: iPhone, $150 billion; Services, $125.2 billion."),
], ids=["us_dotted", "us_canada_rest_of_the_world", "rest_of_world", "us_acronym", "uk_eu", "rest_of_asia_pacific",
        "product_lines"])
def test_every_region_name_that_needs_the_article_gets_it_in_running_text(first, second, line, item):
    """Narration line 2 and the caption list say the article; every drawn label (bars, cards, alt text) keeps the
    name as the company writes it. Mutation-checked by hand (2026-10-19): with `REGION_ARTICLE` back to its four
    Batch 1 names, the U.S., US, UK/EU, rest-of rows turn red."""
    rec = _two_parts(first, second)
    out = compose(rec, THU)
    assert out["video_script"][1] == line
    assert item in _seg_para(out), _seg_para(out)
    assert [b["label"] for b in out["image_spec"]["segments"]] == [first, second]
    assert out["cards"][1]["body"].startswith(f"{first}: 55% of revenue")
    drawn = json.dumps([out["image_spec"], out["cards"], out["opening_card"], out["image_post"]])
    assert f"the {first}" not in drawn and f"The {first}" not in drawn
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("factory", [costco, rivian, comcast, coca_cola, mcdonalds, amazon, lambda: apple(),
                                     lambda: amazon(gross_profit_usd=None),
                                     lambda: costco(gross_profit_usd=None, operating_profit_usd=None),
                                     lambda: rivian(gross_profit_usd=-1_000_000.0),
                                     lambda: rivian(gross_profit_usd=-5_700_000_000.0, operating_profit_usd=None)],
                         ids=["costco", "rivian_gross_loss", "comcast_no_gross", "coca_cola", "mcdonalds", "amazon",
                              "apple", "amazon_no_gross", "costco_no_cost_lines", "tiny_gross_loss",
                              "gross_loss_near_revenue"])
def test_money_map_never_says_as_reported_and_explains_a_gross_line(factory):
    """Batch 1 (plan item 2): "as reported" was false — FMP fills gross profit as revenue minus cost of revenue
    for a company that reports no gross subtotal (Amazon). The image says "annual results", the source paragraph
    says the figures "come from the company's annual financial statements", and whenever a gross line (profit
    or loss) is shown the same paragraph says what gross profit is — never otherwise. Mutation-checked by hand
    (2026-10-15, in memory): the definition on every map, or on none, turns this red. The review of 2026-10-16
    (MM-5): a gross LOSS is explained by its twin, "Gross profit or loss is …" (a loss year showed only "Gross
    loss" and defined "gross profit"); a gross profit of exactly 0 keeps the plain sentence. Mutation-checked by
    hand (2026-10-19): the twin never chosen turns the loss rows red."""
    rec = factory()
    out = compose(rec, THU)
    text = json.dumps(out)
    assert "as reported" not in text.lower()
    assert out["image_spec"]["subtitle"] == f"Fiscal {rec.fiscal_year} · annual results"
    source = T.LEXICON["mm.p.source"].format(fy=rec.fiscal_year)
    gp = rec.gross_profit_usd
    gross = T.LEXICON["mm.p.gross" if gp is None or gp >= 0 else "mm.p.gross.loss"]
    other = T.LEXICON["mm.p.gross.loss" if gp is None or gp >= 0 else "mm.p.gross"]
    last = out["captions"]["linkedin"].split("\n\n")[-1]
    assert last == (f"{source} {gross}" if gp is not None else source), last
    assert (gross in text) is (gp is not None) and other not in text
    assert ("Gross profit" in [b["label"] for b in out["image_spec"]["flow"]]) is (gp is not None and gp >= 0)
    assert ("Gross loss" in [b["label"] for b in out["image_spec"]["flow"]]) is (gp is not None and gp < 0)
    assert T.validate_package(out, record=rec) == []


def test_amazons_aws_line_is_named_everywhere_it_is_drawn():
    """AWS is on `R.SEGMENT_ACRONYMS_OK` (the adapter keeps it): the template draws and lists it like any line."""
    out = compose(amazon(), THU)
    assert "AWS" in [b["label"] for b in out["image_spec"]["segments"]]
    assert "; AWS, $128 billion; " in _seg_para(out)
    assert "AWS $128B" in out["image_post"]["paragraphs"][0]


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
    """Fail closed while `grid` is not in SHIPPED_LAYOUTS (the worker would refuse it): a theme is refused
    `image_spec_invalid` — the day falls back, nothing reaches the worker (a stake drew `pair` until news-v23).
    The plumbing step flips SHIPPED_LAYOUTS with SHIPPED_SERIES; this test then has nothing to prove."""
    monkeypatch.setattr(onscreen, "SHIPPED_LAYOUTS", REAL_SHIPPED_LAYOUTS)
    if {"pair", "grid"} <= set(REAL_SHIPPED_LAYOUTS):
        pytest.skip("pair and grid have shipped")
    for rec, run_date in ((ai_chips(), THEME_RUN),):         # news-v23: stakes draw `rows` (shipped since 2a)
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
    is a member is dropped here too (never shown role-only — the role would still point at them). Owner decisions
    2026-11-20: the week goes on only while three companies remain; two left is `too_few_rows`."""
    member = buy("GE", "General Electric", "director", "Josh Gottheimer", 500_000.0, 2_000.0, 1, [date(2026, 11, 10)])
    member_ceo = replace(member, role="ceo")
    assert R.is_congress_name("Josh Gottheimer")
    out = compose(week(GME, LOW, member_ceo, SBUX), MON)
    assert "Gottheimer" not in json.dumps(out) and "General Electric" not in json.dumps(out)
    assert [r["key"] for r in out["logo_refs"]] == ["GME", "LOW", "SBUX"]
    assert out["image_spec"]["title"].startswith("At least 3 CEOs")
    mixed = compose(week(buy("NKE", "Nike", "cfo", None, 900_000.0, 9_000.0, 1, [date(2026, 11, 10)]), member,
                         buy("HSY", "Hershey", "director", None, 150_000.0, 812.0, 1, [date(2026, 11, 14)]),
                         week_filler(0, "insider_buys", date(2026, 11, 15), 120_000.0), series="insider_buys"), MON)
    assert "Gottheimer" not in json.dumps(mixed) and [r["key"] for r in mixed["logo_refs"]] == ["NKE", "HSY", "WGB"]
    for rows in ((GME, member_ceo), (member, )):
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(week(*rows, series="insider_buys" if rows == (member, ) else "ceo_buys"), MON)
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
        if factory().series in T.FORM4_PERSON_SERIES:
            assert "disclosed buying" in text
        if factory().series in ("thirteen_f", "thirteen_f_spotlight"):
            # news-v23 (the ONE 13F list): "New holding", "Increased to …", "Reduced to …", "Closed out"
            assert re.search(r"\bnew holdings?\b|\breduced\b|\bincreased\b|\bclosed out\b", text,
                             re.IGNORECASE), name


def test_too_few_outlets_is_refused(monkeypatch):
    monkeypatch.setattr(post_copy, "body_budget", lambda *a, **k: 20)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(costco(), THU)
    assert e.value.code == "too_few_outlets"


# ── captions: the budget matrix ───────────────────────────────────────────────


@pytest.mark.parametrize("prefix", [False, True], ids=["prefix_off", "prefix_on"])
@pytest.mark.parametrize("store_state", post_copy.STORE_STATES)
@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_the_caption_budget_matrix(name, factory, run_date, store_state, allow_x_url, prefix):
    # news-v22: every sample × store state × X URL × the first-line prefix switch (B decision 2)
    out = T.compose(factory(), run_date=run_date, store_state=store_state, allow_x_url=allow_x_url,
                    first_line_prefix=prefix)
    # a prefix opens only a caption body and the composed caption that starts with it — never the YouTube title
    for f, body in out["captions"].items():
        if body.startswith(("\U0001F6A8", "\U0001F4CA")):
            assert f != "youtube_title" and out["first_line_prefix"] and body.startswith(out["first_line_prefix"] + " ")
    assert not out["posts"]["youtube"]["title"].startswith(("\U0001F6A8", "\U0001F4CA"))
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
            # the headline only — or, for the 13F LIST (news-v23), the headline and as many WHOLE rows of its own
            # (`_Parts.field_paragraphs`: the rows the picture leaves out, else its rows) as fit, in order; a Threads
            # caption with rows never costs the picture its post
            _first, *rest = body.split("\n\n")
            parts = _parts(factory(), run_date)
            rows = parts.field_paragraphs.get(platform, [])
            # news-v23 phase 3 (a theme's list): or one of the field's own fallback plans (a Threads caption that
            # cannot hold the cover's left-out companies lists the picture's), or a text post's rows from the top
            plans = [rows, *[fb for fb, _need in parts.field_fallbacks.get(platform, [])], parts.text_paragraphs]
            assert any(rest == plan[:len(rest)] for plan in plans), (name, platform)
            if rest and platform == "threads":
                assert len(post["caption"].encode("utf-8")) <= T.THREADS_PICTURE_CAPTION_MAX_BYTES, name
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
    # owner decisions 2026-11-20: the member's row dropped leaves two — the recompose itself is refused (too_few_rows)
    assert got & {"field_mismatch", "facts_invalid", "recompose_refused"}
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
    # news-v22: the frozen first-line prefix switch is an input like allow_x_url; the prefix it chose is compared
    # review 2026-11-20: so are the frozen picture platforms; the pictured outlets written as text posts are compared
    inputs = {"run_date", "store_state", "allow_x_url", "first_line_prefix_on", "picture_outlets"}
    # review of 2026-11-26: `image_post_pre_v23` (an older worker's picture's alt text) is written only where that
    # picture differs — a loss year carries it, compared like every other field
    assert set(T.COMPARED_FIELDS) | inputs == set(out) | {"image_post_pre_v23"}
    assert set(T.COMPARED_FIELDS) | inputs == set(compose(rivian(), THU))
    assert "first_line_prefix" in T.COMPARED_FIELDS and not inputs & set(T.COMPARED_FIELDS)
    assert "text_outlets" in T.COMPARED_FIELDS


def test_the_narration_is_what_the_server_will_check_the_voice_against():
    from app.services.marketing.run_service import narration_words
    out = compose(ceo_roundup(), MON)
    words = narration_words(out)
    assert words[:3] == ["gamestop's", "chief", "executive"] and "$74.4" in words


# ── outliers: long names, extreme amounts, year-crossing windows ──────────────


def test_long_names_fall_to_the_shorter_variants_instead_of_refusing():
    gates = berkshire(filer_name="Bill and Melinda Gates Foundation Trust", filer_symbol=None,
                      moves=(move("OXY", "Occidental Petroleum", "increased", shares=12.0, prev_shares=10.0),
                             move("KR", "Kroger", "decreased", shares=8.0, prev_shares=10.0)),
                      counts=(("increased", 3), ("decreased", 2)))
    out = compose(gates, TUE)
    # news-v23 (the 13F list): the "in …'s latest 13F" forms are 16 and 15 words; the possessive form is exactly 14
    # (the boundary), its counts kept
    assert out["hook"] == "2 of Bill and Melinda Gates Foundation Trust's holding changes: 1 increased, 1 reduced."
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
    # the lead row's own filings in its (video) headline, the week's in the roundup's
    assert "Dec 30, 2026-Jan 2, 2027" in out["captions"]["tiktok"].split("\n")[0]
    assert "Dec 28, 2026-Jan 3, 2027" in out["captions"]["x"]


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
            # owner 2026-11-20 ("3-5", at most 5 — the final review of 2026-11-26): up to INSIDER_MAX_ROWS rows
            for j, name in enumerate(rng.sample(names, rng.randint(1, R.INSIDER_MAX_ROWS))):
                d1 = date(2026, 11, 9) + __import__("datetime").timedelta(days=rng.randrange(0, 7))
                role = "ceo" if series == "ceo_buys" else rng.choice(["cfo", "director"])
                rows.append(buy("ABCDEFGH"[j] + "QZ"[j % 2], name, role, rng.choice(people),
                                rng.choice([100_000.0, 999_950.0, 74_400_000.0, 4_999_999_999.0]),
                                rng.choice([1.0, 850.5, 48_321.0, 2_987_654.0]), rng.randint(1, 40), [d1],
                                holding=rng.choice(["direct", "mixed", "indirect"]), amended=rng.random() < .3))
            rows.sort(key=lambda r: (-r.amount_usd, r.company.name.casefold(), r.company.symbol))
            out.append((week(*rows, series=series), MON))
        elif kind == 1:
            kinds = R.THIRTEEN_F_MOVES
            moves = []
            for j, name in enumerate(rng.sample(names, rng.randint(2, 8))):     # news-v23: a list of 2+
                k = rng.choice(kinds)
                kw = {"newly_reported": dict(shares=1.0, prev_shares=0.0, value_usd=rng.choice([None, 1e6]),
                                             listed_on=rng.choice([None, date(2026, 8, 13)])),
                      "increased": dict(shares=12.0, prev_shares=10.0), "decreased": dict(shares=8.0, prev_shares=10.0),
                      # news-v23: a closed-out row draws the quarter before's count (the adapter always carries it)
                      "no_longer_reported": dict(prev_shares=5.0)}[k]
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
    assert "of combined sales" in out["video_script"][1]
    shares = [int(x) for x in re.findall(r"(\d+)% of", out["video_script"][1])]
    assert shares and max(shares) <= 100
    small = compose(costco(eliminations_usd=-1_000_000_000.0, other_usd=1_000_000_000.0), THU)
    assert "of revenue" in small["video_script"][1]


# ── news-v23 (the restyle, owner-approved 2026-11-19): the picture's short footer per family ──────────────
#
# RESTYLE_SPEC §1, owner: "if we can't remove, then say it shortly". Each series takes its family's source words
# (`T.FOOTER_SOURCE_OF_SERIES`, code-owned in post_copy) and, for the families that date their picture, its own date.

#: The verbatim footer every sample's picture burns (one per sample; the goldens pin them again inside each package).
RESTYLE_FOOTERS = {
    "ceo_roundup": "Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026",
    "berkshire_13f": "Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026",
    "pershing_13f": "Not investment advice · Source: SEC Form 13F · amended Nov 14, 2026",
    "costco_money_map": "Not investment advice · Source: company financial statements · fiscal 2025",
}


def test_restyle_every_series_has_its_family_footer_source():
    assert T.FOOTER_SOURCE_OF_SERIES == {
        "ceo_buys": "SEC Form 4", "insider_buys": "SEC Form 4", "insider_spotlight": "SEC Form 4",
        "thirteen_f": "SEC Form 13F", "thirteen_f_spotlight": "SEC Form 13F",
        "money_map": "company financial statements", "earnings": "company results and analyst consensus",
        "congress_count": "congressional disclosures", "company_stakes": "company filings",
        "theme_explainer": "company filings",
    }
    assert set(T.FOOTER_SOURCE_OF_SERIES) == set(T.SERIES_SPECS)          # no series composes without one
    assert set(T.FOOTER_SOURCE_OF_SERIES.values()) <= set(post_copy.IMAGE_FOOTER_SOURCES)


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_restyle_every_sample_burns_its_familys_short_footer_and_its_kicker_rides_the_cover(name, factory, run_date):
    rec = factory()
    out = compose(rec, run_date)
    footer = out["image_footer"]
    assert out["image_spec"]["footer"] == footer
    source = footer_source_of(rec)
    assert post_copy.image_footer_problem(footer, "template", source=source) is None
    if name in RESTYLE_FOOTERS:
        assert footer == RESTYLE_FOOTERS[name]
    for gone in ("Educational only", "Caydex", "Not affiliated", "Quarter ended", "Fiscal ", "Filed "):
        assert gone not in footer, (name, gone)
    # ... while every CAPTION keeps the full template disclaimer and the non-affiliation line
    for platform, post in out["posts"].items():
        if platform != "youtube":
            assert post_copy.NON_AFFILIATION in post["caption"], platform
    # the worker's contract: the right kicker pill on every VIDEO card is the opening card's, and it is the picture's
    assert out["opening_card"]["kicker"] == out["image_spec"]["kicker"]
    # the picture's strings are exactly the spec's (the brand word is the server's PICTURE_BRAND_TEXT, never a spec
    # string a template writes)
    assert "Caydex" not in onscreen.image_strings(out["image_spec"], out["logo_refs"])


@pytest.mark.parametrize("bad", [
    # news-v22's long footer (what a package composed before the restyle stores)
    ("Educational only · not investment advice · Source: SEC Form 4 filings · Filed Nov 9–15, 2026 · Caydex · "
     "Not affiliated with anyone named"),
    "Not investment advice",                                                     # a lesson's footer
    "Not investment advice · Source: SEC Form 13F · filed Nov 13, 2026",         # another family's
    "Not investment advice · Source: SEC Form 4",                                # its date missing
    "Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026 · Caydex",
    "Not investment advice · Source: SEC Form 4 filings · filed Nov 9–15, 2026",  # the caption's source label
])
def test_restyle_validate_package_refuses_any_footer_but_the_series_short_one(bad):
    out = compose(ceo_roundup(), MON)
    assert T.validate_package(out, record=ceo_roundup()) == []
    tampered = copy.deepcopy(out)
    tampered["image_footer"] = tampered["image_spec"]["footer"] = bad
    found = T.validate_package(tampered, record=ceo_roundup())
    assert {(v["field"], v["code"]) for v in found} >= {("image_footer", "disclaimer_invalid")}, found
    assert T.validate_package(tampered)                                    # without the record too


def test_validation_stays_fast_on_a_huge_tampered_package():
    """A stored row tampered with 200,000-character strings is refused quickly, never a hang on the
    single web worker: an over-long string is refused (MAX_STRING_CHARS) without being scanned, and
    every other check is linear (measured 2026-10-09: ~0.6 s on 400,000 characters before the cap)."""
    import time
    out = compose(ceo_roundup(), MON)
    big = "a · " * 50_000
    out.update(image_footer="Not investment advice · Source: " + big, hook=big)
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
    assert out["image_spec"]["layout"] == layout_of(rec)
    # news-v23 (restyle 2026-11-19): the family's short footer, its source the series' own (a theme's: its angle's)
    head = f"Not investment advice · Source: {footer_source_of(rec)}"
    assert out["image_footer"] == head or out["image_footer"].startswith(head + " · ")
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
        body["counts"][0]["members"] = 4                     # news-v23: the lead company's count
    elif rec.series == "company_stakes":
        body["stakes"][-1]["value_usd"] = body["stakes"][-1]["value_usd"] / 2     # news-v23: the last (smallest) figure
    elif rec.series == "earnings":
        body["reports"][0]["eps_actual"] = body["reports"][0]["eps_actual"] + 0.01
    elif rec.series == "theme_explainer" and rec.angle == "biggest":
        # news-v23 phase 3: the last (smallest) market value — the ranking holds, the captions' figure moves
        body["members"][-1]["market_value_usd"] = body["members"][-1]["market_value_usd"] / 2
    elif rec.series == "theme_explainer" and rec.angle == "growth_profit":
        # fix round 2026-11-20: a figure each of the last three angles DRAWS (their tables never draw top_segment, so
        # that tamper recomposed byte for byte): the lead's prior-year revenue — its growth moves
        body["members"][0]["prior_revenue_usd"] = body["members"][0]["prior_revenue_usd"] * 1.5
    elif rec.series == "theme_explainer" and rec.angle == "insiders":
        body["members"][0]["purchases_usd"] = body["members"][0]["purchases_usd"] + 5e6     # the lead stays first
    elif rec.series == "theme_explainer" and rec.angle == "congress":
        body["members"][0]["congress_members"] = body["members"][0]["congress_members"] + 1  # the lead stays first
    else:
        assert rec.series == "theme_explainer" and rec.angle == "make_money", name    # every other kind: its own branch
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


def test_the_stake_draw_bounds_are_the_adapters():
    """The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL): the adapter refuses at the read every stake text
    this template cannot draw, from COPIES in `company_news_rules` (the adapter may not import this module), pinned
    equal here — a bound changed on one side alone turns this red, never five refused stakes walling the walk again.
    Behaviourally too: at each copied bound the template draws the text, one character past it refuses it, and the
    word measure is the template's own (a run between whitespace)."""
    assert R.STAKE_SOURCE_DRAW_MAX_CHARS == T.STAKE_SOURCE_MAX_CHARS == 80
    assert R.STAKE_TEXT_WORD_MAX_CHARS == T.NAME_WORD_MAX_CHARS == 20
    assert R.STAKE_PAIR_NAME_WORD_MAX_CHARS == T.PAIR_NAME_WORD_MAX_CHARS == 15
    for text in ("", "   ", "NVIDIA 10-Q", "Sherwin-Williams Ventures", "a  bb\tccc\nd", "10-Q/A-Exhibit-99.1-Supplement"):
        assert R.longest_word(text) == T._longest_word(text), text
    assert R.longest_word(None) == 0

    def refused(**kw):
        try:
            compose(stake(**kw), STAKES_RUN)
        except T.NewsTemplateRefused as e:
            return e.code
        return None

    cap, word, pair = R.STAKE_SOURCE_DRAW_MAX_CHARS, R.STAKE_TEXT_WORD_MAX_CHARS, R.STAKE_PAIR_NAME_WORD_MAX_CHARS
    title = "Nscale Form S-1 " + " ".join(c * word for c in "abc")
    title += " " + "d" * (cap - len(title) - 1)
    assert len(title) == cap and R.longest_word(title) == word
    assert refused(source_title=title) is None and refused(source_title=title + "c") == "slot_rejected"
    assert refused(source_title="Nscale Form S-1 " + "a" * (word + 1)) == "slot_rejected"
    assert refused(investee_name="A" * pair + " Labs") is None
    assert refused(investee_name="A" * (pair + 1) + " Labs") == "slot_rejected"
    local = "Taiwan " + "a" * word
    local += " " + "b" * (R.COMPANY_NAME_CHARS[1] - len(local) - 1)
    assert len(local) == R.COMPANY_NAME_CHARS[1] and R.longest_word(local) == word
    # news-v23: a public stake's local listing is never drawn, but the slot rules still hold on it
    assert refused(local_listing=local) is None
    assert refused(local_listing=local + "a") == "slot_rejected"
    assert refused(local_listing="Taiwan " + "a" * (word + 1)) == "slot_rejected"
    # the two record gates the read now applies too: "committed up to" only on a commitment; never dated after the run
    assert refused(value_basis="committed_up_to") == "record_invalid"
    assert refused(verified_on=STAKES_RUN) is None
    assert refused(verified_on=STAKES_RUN + timedelta(days=1)) == "stale_source"


# ── Congress Count ────────────────────────────────────────────────────────────


def test_congress_states_a_disclosed_count_dated_by_its_month_and_no_dollars():
    """news-v23 (owner 2026-11-19: "2-3 companies at once", congress_multi3.jpg): ONE post of the month's companies —
    the hook the lead's count, the picture and the cover a card per company (its count, its line), every caption's
    first line dated by the month, no dollar anywhere, the month's ledger key (one post a month, each company once)."""
    out = compose(congress(), CONGRESS_RUN)
    assert out["hook"] == "3 members of Congress disclosed purchases of Accenture stock in November 2026."
    assert out["opening_card"]["kicker"] == out["image_spec"]["kicker"] == "CONGRESS · DISCLOSED IN NOVEMBER"
    spec = out["image_spec"]
    assert spec["layout"] == "rows" and spec["title"] == out["opening_card"]["headline"] == (
        "Members of Congress disclosed purchases")
    assert spec["subtitle"] == "November 2026 · disclosures as of Dec 8, 2026"
    assert spec["notes"] == ["Each member counted once per company"]
    rows = [{"logo": s, "chip": s, "cells": [n, f"members disclosed purchases of {n} stock", c]}
            for s, n, c in (("ACN", "Accenture", "3"), ("CTSO", "Contoso", "2"), ("NWT", "Northwind", "2"))]
    assert spec["sections"] == [{"rows": rows}] and out["opening_card"]["list"] == {"rows": rows}
    assert "figure" not in out["opening_card"] and "chip" not in out["opening_card"]
    for f, body in out["captions"].items():
        head = body.split("\n", 1)[0]
        assert "disclosed purchases of Accenture" in head and ("November 2026" in head or "Nov 2026" in head), f
    assert out["captions"]["x"].split("\n\n")[0] == ("3 members of Congress disclosed purchases of Accenture stock "
                                                     "in November 2026, 2 of Contoso and 2 of Northwind.")
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
    (congress(lead=_unchecked(congress_row(), members=1)), CONGRESS_RUN, "too_few_rows"),  # "1 member" points at one
    (congress(lead=_unchecked(congress_row(), members=0)), CONGRESS_RUN, "too_few_rows"),
    (congress(lead=_unchecked(congress_row(), members=True)), CONGRESS_RUN, "too_few_rows"),
    (congress(lead=_unchecked(congress_row(), members=536)), CONGRESS_RUN, "too_few_rows"),
    (congress(month="2026-10", fetched_on=date(2026, 11, 10)), CONGRESS_RUN, "stale_source"),   # not last month
    (congress(), date(2027, 1, 12), "stale_source"),                       # a month later
    (congress(fetched_on=date(2026, 12, 6)), CONGRESS_RUN, "stale_source"),  # read < 7 days after the month
    (congress(fetched_on=date(2026, 12, 9)), CONGRESS_RUN, "stale_source"),  # read after the run
    (congress(fetched_on=date(2026, 12, 7)), date(2026, 12, 15), "stale_source"),  # read 8 days before the run
    (congress(company=_unchecked(co("ACN", "Accenture"), name="Signal Hill")), CONGRESS_RUN, "slot_rejected"),
    # a 21-char word: since the review of 2026-10-16 the record refuses it too (`R.COMPANY_NAME_WORD_MAX_CHARS`),
    # so it is built unchecked here to prove the template's own refusal still stands behind it
    (congress(company=_unchecked(co("ACN", "Accenture"), name="Supercalifragilisticex Co")), CONGRESS_RUN,
     "slot_rejected"),
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
    "image_spec.notes[0]": lambda o, w: o["image_spec"]["notes"].__setitem__(0, f"{w} members"),
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


@pytest.mark.parametrize("mutate", [
    lambda o: o["image_spec"]["sections"][0]["rows"][1]["cells"].__setitem__(-1, "1"),
    lambda o: o["image_spec"]["sections"][0]["rows"][2]["cells"].__setitem__(-1, "0"),
    lambda o: o["image_spec"]["sections"][0]["rows"][0]["cells"].__setitem__(-1, "two"),
    lambda o: o["image_spec"]["sections"][0]["rows"][0]["cells"].pop(),
    lambda o: o["opening_card"]["list"]["rows"][1]["cells"].__setitem__(-1, "1"),
    lambda o: o["opening_card"]["list"]["rows"][0].pop("cells"),
], ids=["picture_one", "picture_zero", "picture_word", "picture_no_count", "cover_one", "cover_no_cells"])
def test_news_v23_every_congress_row_states_at_least_two_members(mutate):
    """news-v23: the Congress Count is ONE list of 2-3 companies; each row's count cell — on the picture AND on the
    cover's list — is a count of at least two distinct members (one points at a findable person), checked per row
    (`congress_wording`), whatever the hook says."""
    out = compose(congress(), CONGRESS_RUN)
    assert _codes(out, congress()) == set()
    assert [r["cells"][-1] for r in out["image_spec"]["sections"][0]["rows"]] == ["3", "2", "2"]
    assert [r["cells"][-1] for r in out["opening_card"]["list"]["rows"]] == ["3", "2", "2"]
    mutate(out)
    assert "congress_wording" in _codes(out, congress())


def test_news_v23_a_refused_congress_company_is_dropped_never_the_post():
    """`drop_refused_rows` on a Congress month: a company whose count the template refuses (here a forged count of
    ONE member) leaves the post and the rest stay in order; with one left the month is `too_few_rows`; a month that
    is not the run's is refused whole (never narrowed)."""
    three = congress(lead=_unchecked(congress_row(), members=1), others=2)
    narrowed = T.drop_refused_rows(three, CONGRESS_RUN)
    assert [c.company.symbol for c in narrowed.counts] == ["CTSO", "NWT"]
    out = compose(narrowed, CONGRESS_RUN)
    assert out["source_ref"] == "news:congress_count:2026-11" and "Accenture" not in json.dumps(out)
    with pytest.raises(T.NewsTemplateRefused) as e:
        T.drop_refused_rows(congress(lead=_unchecked(congress_row(), members=1), others=1), CONGRESS_RUN)
    assert e.value.code == "too_few_rows"
    ok = congress()
    assert T.drop_refused_rows(ok, CONGRESS_RUN) is ok
    with pytest.raises(T.NewsTemplateRefused) as e:
        T.drop_refused_rows(ok, CONGRESS_RUN + timedelta(days=40))
    assert e.value.code != "too_few_rows"


def test_congress_revalidate_refuses_a_count_below_two_in_the_facts():
    rec = congress()
    out = compose(rec, CONGRESS_RUN)
    facts = sheet(rec)
    facts["record"]["counts"][0]["members"] = 1
    assert {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=CONGRESS_RUN)} == {"facts_invalid"}


# ── Company Stakes ────────────────────────────────────────────────────────────
#
# news-v23 (the restyle, owner 2026-11-19: "filter for companies that public only. no private" + "add 1-3 more
# companies for the NVIDIA (or the other trillion companies)"; stakes_nvidia.jpg / stakes_nvidia3.jpg): ONE investor's
# stakes in PUBLIC companies a post (`R.CompanyStakes`, 1-8), a `rows` list on the picture and the cover. A bare
# `stake(...)` row below is composed as its one-stake post (`compose` / `_parts` wrap it).


@pytest.mark.parametrize("factory, hook, label, l3", [
    (intel_stake, "Nvidia invested $5 billion in Intel.", "invested", "Shares of Intel are listed on a U.S. exchange."),
    (public_commitment, "Nvidia committed up to $1 billion to Contoso.", "committed up to",
     "A commitment is money agreed to be invested, which may be paid over time."),
    # news-v17: a carrying or fair value is the source's figure — the headline names no filer
    (tesla_spacex, "Tesla's SpaceX stake had a fair value of $3 billion.", "fair value",
     "Shares of SpaceX are listed on a U.S. exchange."),
    (lambda: stake(investor=co("BRK-B", "Berkshire Hathaway"), investee_name="Fabrikam", investee=co("FBRK", "Fabrikam"),
                   value_usd=461_000_000.0, value_basis="carrying_value", kind="us_listed_off_13f"),
     "Berkshire Hathaway's Fabrikam stake had a carrying value of $461 million.", "carrying value",
     "Shares of Fabrikam are listed on a U.S. exchange."),
], ids=["invested_us", "committed", "fair_value_us", "carrying_value"])
def test_stake_verb_table(factory, hook, label, l3):
    out = compose(factory(), STAKES_RUN)
    (row,) = out["image_spec"]["sections"][0]["rows"]
    assert out["hook"] == hook and row["label"] == label and out["video_script"][2] == l3
    assert out["opening_card"]["kicker"] == out["image_spec"]["kicker"] == "COMPANY STAKES"
    assert out["opening_card"]["list"]["rows"] == [row]                       # the cover is the picture's list
    text = json.dumps(out)
    assert not T.STAKES_BANNED_RE.search(fold(text)) and not re.search(r"\b(?:bet|bets|worth|bought)\b", text, re.I)
    assert row["cells"][1].startswith("As of ")                              # the figure's own date, always


def test_a_stake_never_says_its_ownership_share_and_drops_a_bad_background():
    out = compose(berkshire_mixed(), STAKES_RUN)
    text = json.dumps(out)
    assert "50%" not in text and "50.0" not in text and " owns" not in text
    assert "Jefferies" not in text                                         # its "50%" background is dropped
    spacex = json.dumps(compose(tesla_spacex(), STAKES_RUN))
    assert "under 1%" not in spacex and "xAI" not in spacex


def test_the_stake_of_twin_and_the_short_fair_value_for_a_possessive_investor():
    rec = stake(investor=co("MCO", "Moody's"), investee_name="Fabrikam Analytics",
                investee=co("FBKA", "Fabrikam Analytics"), value_usd=3_200_000_000.0, value_basis="fair_value",
                kind="us_listed_off_13f", as_of=date(2026, 3, 27))
    parts = _parts(rec, STAKES_RUN)
    assert parts.opening["headline"] == parts.image_spec["title"] == "Stakes of Moody's in public companies"
    assert "The Fabrikam Analytics stake of Moody's had a fair value of $3.2 billion, as of Mar 27." in (
        parts.image_headlines)
    out = compose(rec, STAKES_RUN)
    assert "Moody's's" not in json.dumps(out) and "Moody's'" not in json.dumps(out)
    # two stakes: the count's ".of" twins too
    two = stakes(rec, replace(rec, stake_id="6c8e0a2c-4e6a-4c8e-9a1c-3e5a7c9e1b0f", investee_name="Contoso",
                              investee=co("CTSO", "Contoso"), value_usd=1_000_000_000.0))
    out = compose(two, STAKES_RUN)
    assert out["captions"]["x"].startswith("The stakes of Moody's in 2 public companies")
    assert "Moody's's" not in json.dumps(out) and "Moody's'" not in json.dumps(out)


@pytest.mark.parametrize("rec, run_date, code", [
    (stake(value_usd=None, value_basis=None, ownership_pct=10.7), STAKES_RUN, "record_invalid"),   # a share alone
    (_unchecked(intel_stake(), investee_name="Private companies (not named)"), STAKES_RUN, "record_invalid"),
    (_unchecked(intel_stake(), investee_name="Investment commitments not named"), STAKES_RUN, "record_invalid"),
    (stake(kind="commitment", value_basis="invested"), STAKES_RUN, "record_invalid"),
    (stake(kind="us_listed_off_13f", value_basis="committed_up_to"), STAKES_RUN, "record_invalid"),
    (stake(investee=co("NVDA", "NVIDIA")), STAKES_RUN, "record_invalid"),   # a stake in itself
    (stake(investee_name="NVIDIA"), STAKES_RUN, "record_invalid"),
    (stake(listed_since=date(2027, 1, 4)), STAKES_RUN, "record_invalid"),
    # news-v23 (owner 2026-11-19 "public only"): no verified U.S. listing, or a kind that is never public
    (stake(investee=None), STAKES_RUN, "record_invalid"),
    (stake(kind="private"), STAKES_RUN, "record_invalid"),
    (stake(kind="non_us_listed", local_listing="Japan"), STAKES_RUN, "record_invalid"),
    (intel_stake(), date(2027, 1, 23), "stale_source"),                    # verified 121 days before the run
    (stake(as_of=date(2023, 12, 1), verified_on=date(2026, 12, 1)), STAKES_RUN, "stale_source"),   # > 3 years
    (stake(as_of=date(2026, 12, 30), verified_on=date(2026, 12, 30)), STAKES_RUN, "stale_source"),
    (stake(investee_name="Nanofabricationsystems"), STAKES_RUN, "slot_rejected"),    # a 22-char word
    (stake(investee_name="Optoelectronics Holdings"), STAKES_RUN, None),               # 15: draws whole
    (stake(investee_name="Telecommunications Holdings"), STAKES_RUN, "slot_rejected"),  # 18 > 15
    (stake(investee_name="Northwind Data Center Partners Holdings Co"), STAKES_RUN, "slot_rejected"),   # 41 chars
    (stake(source_title=("Northwind 10-K " + "(fiscal year ended Dec 31, 2025) " * 3).strip()), STAKES_RUN,
     "slot_rejected"),
    (stake(source_title="See https://example.com/filing"), STAKES_RUN, "slot_rejected"),
    (stake(source_title="Northwind 10-K Supercalifragilisticexpialidocious"), STAKES_RUN, "slot_rejected"),
    (stake(local_listing="Japan 100%"), STAKES_RUN, "slot_rejected"),
], ids=["pct_only", "aggregate", "not_named", "commitment_invested", "committed_listed", "self_symbol", "self_name",
        "listed_after_run", "no_listing", "kind_private", "kind_non_us", "verified_stale", "as_of_old",
        "dated_after_run", "word_22", "word_15_ok", "word_18", "name_41", "source_long", "source_link", "source_word",
        "local_percent"])
def test_stake_refusals(rec, run_date, code):
    if code is None:
        assert compose(rec, run_date)["image_spec"]["sections"][0]["rows"][0]["cells"][0] == rec.investee_name
        return
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == code


def test_news_v23_a_private_unlisted_or_not_public_stake_cannot_be_a_row_of_the_record():
    """The record itself refuses a stake no public company holds (owner 2026-11-19: "filter for companies that public
    only. no private"): no verified listing, a kind that is never public (`R.PUBLIC_STAKE_KINDS`), another investor's
    stake, no figure — and a post's rows are ranked (`R.stake_rank_key`). Mutation-checked by hand (2026-11-20): the
    `investee` check removed from `CompanyStakes.__post_init__`, the first case composes as a record."""
    for row, why in ((stake(investee=None), "public only"), (stake(kind="private"), "never public"),
                     (stake(kind="non_us_listed"), "never public"),
                     (stake(investor=co("TSLA", "Tesla")), "investor's"),
                     (stake(value_usd=None, value_basis=None, ownership_pct=10.0), "figure")):
        with pytest.raises(ValueError) as e:
            R.CompanyStakes(series="company_stakes", investor=co("NVDA", "NVIDIA"), stakes=(row,))
        assert why in str(e.value), (why, str(e.value))
    with pytest.raises(ValueError, match="ranked"):
        R.CompanyStakes(series="company_stakes", investor=co("NVDA", "NVIDIA"),
                        stakes=(synopsys_stake(), intel_stake()))
    with pytest.raises(ValueError, match="1..8|outside"):
        R.CompanyStakes(series="company_stakes", investor=co("NVDA", "NVIDIA"), stakes=())


# ── Batch 1 (2026-10-15, plan item 4): no drawn stake text names a person ──────────────────────────
#
# The source title (the picture's as-of line, card 4, every caption) and the local listing are drawn on every stake
# post, and under the 2026-10-09 rules a person is named only in written body text. Each case below is caught by
# exactly the check its comment names (both mutation-checked); the record's own companies are masked first. news-v23:
# every investee is a LISTED company now (its profile verified it), so its name is masked like the investor's — the
# unlisted-investee check still runs on a row the record could not carry (`T._stake_person_problem` directly).


@pytest.mark.parametrize("rec, field", [
    # `_names_a_person` and the person lexicon both
    (stake(source_title="Jensen Huang keynote (Mar 18, 2026)"), "source title"),
    (stake(source_title="Warren Buffett's 2025 shareholder letter"), "source title"),
    # the person lexicon alone: a lone surname
    (stake(source_title="Huang keynote (Mar 18, 2026)"), "source title"),
    # `_names_a_person` alone: a given name before a capitalised word, in no lexicon
    (stake(local_listing="Masayoshi Son"), "local listing"),
], ids=["given_name_and_surname", "possessive_letter", "lone_surname", "local_listing"])
def test_a_stake_whose_drawn_text_names_a_person_is_refused(rec, field):
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, STAKES_RUN)
    assert (e.value.code, e.value.detail) == ("slot_rejected", f"{field}: names a person")
    assert not any(w in str(e.value) for w in ("Jensen", "Huang", "Buffett", "Masayoshi"))


@pytest.mark.parametrize("name", ["Charles Schwab", "Bezos Expeditions", "Gates Industrial"])
def test_an_unlisted_investee_named_for_a_person_is_still_caught_by_the_person_check(name):
    """A row with no verified listing never reaches a record (news-v23); the person check still reads its name — the
    defence the template keeps for a row that slipped past (`_stake_person_problem`)."""
    assert T._stake_person_problem(stake(investee_name=name, investee=None), "Intel 8-K (Dec 29, 2025)") == (
        "investee name")


@pytest.mark.parametrize("rec", [
    # a LISTED investee keeps its profile name (its listing verified it is a company) …
    stake(investee_name="Charles Schwab", investee=co("SCHW", "Charles Schwab"), kind="us_listed_off_13f"),
    # … and its own name in the source title is masked, as is the investor's
    stake(investee_name="Charles Schwab", investee=co("SCHW", "Charles Schwab"), kind="us_listed_off_13f",
          source_title="Charles Schwab 8-K (Dec 1, 2026)"),
    stake(investor=co("LLY", "Eli Lilly"), source_title="Eli Lilly 2025 Form 10-K"),
    stake(investor=co("MS", "Morgan Stanley"), source_title="Morgan Stanley 10-Q (Sep 30, 2026)"),
    # the review of 2026-10-16 (ST-1): a listed company named with a stake-source surname is masked like any
    # other ("Gates" is on `R.STAKE_SOURCE_PERSON_SURNAMES`)
    stake(investee_name="Gates Industrial", investee=co("GTES", "Gates Industrial"), kind="us_listed_off_13f",
          source_title="Gates Industrial 2025 Form 10-K"),
], ids=["listed_investee", "listed_investee_in_source", "investor_in_source", "investor_given_name",
        "listed_investee_with_a_listed_surname"])
def test_a_company_named_for_a_person_is_still_a_company(rec):
    out = compose(rec, STAKES_RUN)
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][0] == rec.investee_name


def test_every_shipped_stake_sample_passes_the_person_check():
    rows = [r for f in (nvidia_stakes_more, berkshire_mixed) for r in f().stakes]
    for rec in rows + [tesla_spacex(), intel_stake(), public_commitment(), lumentum_stake()]:
        assert T._stake_person_problem(rec, rec.source_title) is None, rec.investee_name


@pytest.mark.parametrize("rec, field", [
    # the review of 2026-10-16 (ST-1): every one of these passed `_names_a_person` and the lesson lexicons (the
    # review's probe) — the stake-source surnames catch them, in every drawn text
    (stake(source_title="Dario Amodei essay (Oct 2, 2026)"), "source title"),
    (stake(source_title="Amodei essay (Oct 2, 2026)"), "source title"),
    (stake(source_title="Altman blog (Sep 3, 2026)"), "source title"),
    (stake(source_title="Mira Murati interview"), "source title"),
    (stake(source_title="Ilya Sutskever interview"), "source title"),
    (stake(source_title="Ellison interview (Jun 12, 2026)"), "source title"),
    (stake(source_title="Abel letter (Feb 28, 2026)"), "source title"),
    (stake(local_listing="Altman Holdings"), "local listing"),
], ids=["dario_amodei", "amodei", "altman", "murati", "sutskever", "ellison", "abel", "local_listing"])
def test_a_stake_naming_a_stake_source_person_is_refused(rec, field):
    """The source line is drawn on the picture, card 4 and every caption — and a person is never drawn (marketing.md
    §1 decision 2). Mutation-checked by hand (2026-10-19): with `stake_person_surname_hits` dropped from
    `_stake_person_problem`, every row here composes."""
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, STAKES_RUN)
    assert (e.value.code, e.value.detail) == ("slot_rejected", f"{field}: names a person")
    assert not any(w in str(e.value) for w in ("Amodei", "Altman", "Murati", "Sutskever", "Ellison", "Abel", "Gates"))


# ── the review of 2026-10-16: a theme's regional fact; the cover's word cap in every template ──────────


def test_a_themes_regional_fact_reads_as_a_region_and_the_explainer_says_so():
    """TH-1: a member whose breakdown is regional (Costco's countries) was narrated "… largest revenue line in
    fiscal 2026 was United States." under an explainer calling a revenue line "one product or business". Now the
    explainer names regions too, the narrated fact takes "the" (`R.REGION_ARTICLE`), and every drawn label — the
    tile, the card — keeps the name as written. Mutation-checked by hand (2026-10-19): with the article dropped
    from `sp()`, the narration assert turns red."""
    rows = _members(6, seg=lambda i: "United States" if i == 0 else ("Cloud" if i < 3 else None),
                    share=lambda i: 0.73 if i == 0 else (0.5 if i < 3 else None))
    rec = theme(rows, theme_size=6)
    out = compose(rec, THEME_RUN)
    script = " ".join(out["video_script"])
    assert "largest revenue line in fiscal 2026 was the United States" in script, script
    assert T.LEXICON["th.explainer"] in out["video_script"]
    assert "region" in T.LEXICON["th.explainer"] and "region" in T.LEXICON["th.c.explainer.body"]
    tile = next(t for t in out["image_spec"]["tiles"] if t["logo"] == rows[0][0])
    assert tile["line"] == "United States · 73% of revenue"          # news-v23 phase 3: "… of revenue" on the tile
    drawn = json.dumps([out["image_spec"], out["cards"], out["opening_card"], out["image_post"]])
    assert "the United States" not in drawn and "The United States" not in drawn
    assert "product or business" not in json.dumps(out) and "product or business" not in json.dumps(_offered(rec))
    assert T.validate_package(out, record=rec) == []


def test_a_period_final_revenue_line_never_doubles_the_stop():
    """The re-review of 2026-10-19 (TH-PUNCT-1): a narration line ends on its revenue line and the entry's own period
    ("… and {m2_s} was {g2}."), so Walmart's "Walmart U.S." (the F5 label override) and the article path's "the U.S."
    printed "Walmart U.S.." and "the U.S..", narrated and burned into the video's captions. `_t` lets a period-final
    value end the sentence itself; the lexicon is unchanged, and a mid-sentence label keeps its comma. Mutation-
    checked by hand (2026-10-19): `_t` back to a plain `format_map` turned the narration asserts red."""
    rows = [("COST", "Costco", "Food and Sundries", 0.39, "2025"), ("WMT", "Walmart", "Walmart U.S.", 0.68, "2025"),
            ("NKE", "Nike", "U.S.", 0.42, "2025"), ("TGT", "Target", "Food & beverage", 0.23, "2025"),
            ("KR", "Kroger", "Grocery", 0.5, "2025"), ("DG", "Dollar General", "Consumables", 0.82, "2025")]
    rec = theme(rows, title="Big retail", slug="big-retail", theme_size=6)
    out = compose(rec, THEME_RUN)
    assert out["video_script"][0] == ("Costco's largest revenue line in fiscal 2025 was Food and Sundries, and "
                                      "Walmart's was Walmart U.S.")
    # 2026-11-20: a theme's segment names in title case ("Food & beverage" as filed → "Food & Beverage")
    assert out["video_script"][1] == ("Nike's largest revenue line in fiscal 2025 was the U.S., and Target's was "
                                      "Food & Beverage.")
    assert ".." not in json.dumps(out) and T.validate_package(out, record=rec) == []
    # one fact on its line: "{g1}." ends the line too
    assert T._t("th.one.poss", m1_s="Walmart's", fy="2025", g1="Walmart U.S.") == (
        "Walmart's largest revenue line in fiscal 2025 was Walmart U.S.")
    assert T._t("th.one.poss", m1_s="Costco's", fy="2025", g1="Food and Sundries") == (
        "Costco's largest revenue line in fiscal 2025 was Food and Sundries.")
    with pytest.raises(KeyError):                                     # a missing slot is still a bug, never a gap
        T._t("th.one.poss", m1_s="Costco's", fy="2025")


def test_a_company_name_word_the_cover_cannot_draw_is_refused_by_every_template():
    """The record refuses a company-name word longer than `R.COMPANY_NAME_WORD_MAX_CHARS` (a CardOverflow on the
    cover skips the whole day); the Form 4 and Money Map templates now refuse it too, as 13F and the 2b series
    did — built unchecked here, the shape of a record that slipped past the gate. A 20-letter word composes.
    Mutation-checked by hand (2026-10-19): either template's `_name_slot` call removed turns its row red."""
    assert T.NAME_WORD_MAX_CHARS == T.F13_MAX_WORD_CHARS == R.COMPANY_NAME_WORD_MAX_CHARS == 20
    long = "Supercalifragilisticex Co"                          # a 22-letter word
    for rec, run in ((costco(company=_unchecked(co("COST", "Costco"), name=long)), THU),
                     (week(replace(GME, company=_unchecked(GME.company, name=long))), MON)):
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(rec, run)
        assert e.value.code == "slot_rejected" and "20 characters" in e.value.detail, e.value.detail
    fits = "Electrometallurgical Co"                            # 20 letters
    for rec, run in ((costco(company=co("COST", fits)), THU), (week(replace(GME, company=co("GME", fits))), MON)):
        out = compose(rec, run)
        assert T.validate_package(out, record=rec) == []


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
    # news-v16 (review round 16): a company's worth by a verb, or its rank — dropped; their honest twins kept
    ("Contoso led the round, which values Nscale at $90 billion.", False),                     # STAKE_CLAIM_ROWS[0]
    ("NVIDIA values the partnership with Nscale, it said in 2025.", True),
    ("Nscale is the largest AI startup in Europe.", False),                                     # STAKE_CLAIM_ROWS[1]
    ("Nscale is the fastest-growing cloud company.", False),
    ("The largest tranche closed on Oct 8, 2025.", True),
    ("Nscale is the largest in Europe.", False),                                                # STAKE_CLAIM_ROWS[2]
    ("The largest of the two tranches closed on Oct 8, 2025.", True),
]


@pytest.mark.parametrize("background, kept", BACKGROUNDS, ids=[str(i) for i in range(len(BACKGROUNDS))])
def test_a_stake_background_is_caption_text_only_and_only_when_every_rule_passes(background, kept, monkeypatch):
    """A3: the background (hand-written, unnormalised) appears only in long-caption paragraphs —
    never drawn, narrated, carded or used as alt text. Mutation-checked by hand (2026-10-10, in memory):
    without `_PER_SHARE_RE` in `_stake_background` case 2 ("at $414.79 a share") turned red — case 3
    is refused by its "Acquired" as well. news-v19: only while STAKE_BACKGROUND_IN_CAPTIONS is on — the
    rules here are what that switch would run (its default, off, is pinned below)."""
    monkeypatch.setattr(T, "STAKE_BACKGROUND_IN_CAPTIONS", True)
    rec = stake(background=background, investee_name="Nscale", investee=co("NSCL", "Nscale"), kind="us_listed_off_13f",
                source_title="Nscale Form S-1 (Sep 18, 2026)")
    out = compose(rec, STAKES_RUN)
    assert T.validate_package(out, record=one(rec)) == []
    in_captions = [f for f, body in out["captions"].items() if background in body]
    if kept:
        assert set(in_captions) >= {"linkedin", "facebook", "instagram", "youtube_description"}
        assert not set(in_captions) & {"x", "bluesky", "threads", "youtube_title"}
    else:
        assert in_captions == []
    video_and_image = json.dumps([out["hook"], out["video_script"], out["cards"], out["opening_card"],
                                  out["image_spec"], out["image_post"]])
    assert background not in video_and_image


def test_by_default_no_stake_background_reaches_any_caption():
    """news-v19 (Batch 1, plan item 5): the live preview of 2026-10-14 printed the catalogue's hand-kept
    fragments as caption paragraphs ("Paid $30.0M for a convertible note …", "Rest of a $20.0B facility …"),
    in their own number style. STAKE_BACKGROUND_IN_CAPTIONS is off by default: every stake — each sample, and a
    sample carrying EVERY background of the real catalogue the rules would keep — composes exactly as it does
    with no background at all. Mutation-checked by hand (2026-10-15): with the switch's default set to True,
    this turned red (the Nscale, Intel, ITOCHU and Iroquois samples' captions carried their backgrounds)."""
    assert T.STAKE_BACKGROUND_IN_CAPTIONS is False
    seed = json.loads((_BACKEND / "data" / "trillion_club_seed.json").read_text(encoding="utf-8"))
    kept = [b for b in (s.get("background") for s in seed["stakes"]) if isinstance(b, str)
            and T._stake_background(b, ["NVIDIA", "Nscale", "Nscale Form S-1 (Sep 18, 2026)"]) is not None]
    assert len(kept) >= 20                     # the catalogue really has backgrounds the rules keep
    recs = [one(f()) for f in (tesla_spacex, intel_stake, public_commitment)] + [nvidia_stakes_more(), berkshire_mixed()]
    recs += [one(stake(background=b)) for b in kept]
    for rec in recs:
        out = compose(rec, STAKES_RUN)
        bare = replace(rec, stakes=tuple(replace(r, background=None) for r in rec.stakes))
        assert out == compose(bare, STAKES_RUN), rec.stakes[0].investee_name
        for r in rec.stakes:
            if r.background:
                assert r.background not in json.dumps(out, ensure_ascii=False), r.investee_name
        assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("kind, listed, l3, c3", [
    ("us_listed_off_13f", None, "Shares of Nscale are listed on a U.S. exchange.",
     ("Listed in the U.S.", "Shares trade on a U.S. exchange")),
    ("on_13f_note", None, "Shares of Nscale are listed on a U.S. exchange.",
     ("Listed in the U.S.", "Shares trade on a U.S. exchange")),
    ("us_listed_off_13f", date(2026, 10, 1), "Nscale has been listed on an exchange since October 2026.",
     ("Listed since", "Oct 2026")),                                                # listed_since overrides
], ids=["us_off_13f", "us_on_13f", "listed_since"])
def test_stake_listing_lines_say_only_what_the_record_carries(kind, listed, l3, c3):
    """news-v23: every stake is a public company's — the one-stake post says its U.S. listing (or since when); the
    private / non-U.S. / unverified lines are gone with those stakes."""
    out = compose(stake(kind=kind, investee_name="Nscale", investee=co("NSCL", "Nscale"), listed_since=listed),
                  STAKES_RUN)
    assert out["video_script"][2] == l3
    assert (out["cards"][2]["title"], out["cards"][2]["body"]) == c3
    assert [r["key"] for r in out["logo_refs"]] == ["NVDA", "NSCL"]
    for f in ("st.l3.private", "st.l3.non_us", "st.l3.source", "st.c3.private.title", "st.c3.non_us.title"):
        assert f not in T.LEXICON


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
        out = compose(nvidia_stakes(), STAKES_RUN)
        out["video_script"][3] = f"Companies {word} stakes like this in filings and official releases."
        assert {"series_word", "banned_word", "off_table_verb"} & _codes(out, nvidia_stakes()), word


# ── Earnings vs Estimates ─────────────────────────────────────────────────────
#
# news-v23 (the restyle, owner 2026-11-19: "add Revenue reported and estimate too" + "combine 2 to 4 companies at once"
# + "add +X% surprised (in green) or -X% surprised (in red)" + the column order "Estimate, Reported, Surprise";
# earnings_multi4.jpg / earnings_multi2.jpg): ONE post of 2-4 companies (`R.EarningsDay`), each an EPS and a Revenue
# line of three columns. "Revenue always" (the owner ACCEPTS the GE-type basis risk: `EARNINGS_REVENUE_IN_POSTS`).


#: A profit or loss stated as the company's (review round 9): the calendar's EPS is the figure on the
#: basis analysts estimate — often not the official accounting result — so no line may say the
#: company "earned" / "lost" or had "earnings of" / "a loss of" / "in earnings" (nor any "profit").
_PROFIT_LOSS_RE = re.compile(r"(?i)\bearned\b|\blost\b|\bloss(?:es)?\b|\bearnings\s+(?:of|per)\b|\bin\s+earnings\b|"
                             r"\bprofit")
MINUS = "−"


def _table(out, where="image_spec"):
    """[(name, chip, [[label, est, rep, surprise], …]), …] — the picture's (or the cover's) table, row by row."""
    sec = out["image_spec"]["sections"][0] if where == "image_spec" else out["opening_card"]["list"]
    return [(r["cells"][0], r.get("chip"), [ln["cells"] for ln in r["table"]]) for r in sec["rows"]]


def test_news_v23_the_earnings_post_is_one_table_of_its_companies():
    """The sample (Northwind Fitness, a smaller loss than estimated; Contoso, revenue under its estimate), composed:
    the picture is ONE `rows` section naming its columns "Estimate" | "Reported" | "Surprise", a row per company in
    the record's order (the adapter's largest first) — its logo, ticker chip and name, and its table's EPS and
    Revenue lines (the estimate, the reported figure, the surprise; a negative figure with U+2212) — then the basis
    note; the cover's `list` is the same table; the ledger key names EVERY company (each posted once)."""
    rec = earnings()
    out = compose(rec, EARNINGS_RUN)
    spec = out["image_spec"]
    assert spec["layout"] == "rows" and spec["kicker"] == "EARNINGS VS ESTIMATES"
    assert spec["title"] == "Reported vs analyst estimates" and spec["subtitle"] == "Reported Nov 5, 2026"
    assert spec["sections"][0]["columns"] == ["Estimate", "Reported", "Surprise"]
    assert _table(out) == [
        ("Northwind Fitness", "NWFT", [["EPS", f"{MINUS}$0.12", f"{MINUS}$0.05", "+58.3%"],
                                       ["Revenue", "$543.6M", "$551.9M", "+1.5%"]]),
        ("Contoso", "CTSO", [["EPS", "$1.05", "$1.12", "+6.7%"], ["Revenue", "$3.3B", "$3.2B", f"{MINUS}3.0%"]]),
    ]
    assert [r["logo"] for r in spec["sections"][0]["rows"]] == ["NWFT", "CTSO"]
    assert spec["notes"] == [T.LEXICON["er.img.note"]]
    assert spec["footer"] == "Not investment advice · Source: company results and analyst consensus"
    card = out["opening_card"]
    assert card["headline"] == spec["title"] and card["logos"] == ["NWFT"] and "chip" not in card
    assert card["list"] == {"columns": spec["sections"][0]["columns"], "rows": spec["sections"][0]["rows"]}
    assert out["source_ref"] == "news:earnings:NWFT:2026-11-05+CTSO:2026-11-05"
    assert R.earnings_ref_items(out["source_ref"]) == [("NWFT", date(2026, 11, 5)), ("CTSO", date(2026, 11, 5))]
    assert out["logo_refs"] == [{"key": "NWFT", "name": "Northwind Fitness"}, {"key": "CTSO", "name": "Contoso"}]
    assert T.validate_package(out, record=rec) == []
    assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=EARNINGS_RUN) == []


@pytest.mark.parametrize("n", [2, 3, 4])
def test_news_v23_two_to_four_companies_each_get_a_row_a_line_a_card_and_a_caption_paragraph(n):
    """2, 3 and 4 companies: a picture row each, the cover lists every one, the narration says the first three in a
    line each (the hook first), then ALWAYS the basis note — and with two, the estimate's definition (review
    2026-11-20: a fourth company is on the cover and in every caption, never in place of the caveat) — each narrated
    company has its card, and every long caption carries each company's paragraph (`required_paragraphs`)."""
    rec = earnings(others=n - 1)
    out = compose(rec, EARNINGS_RUN)
    names = [r.company.name for r in rec.reports]
    assert [r[0] for r in _table(out)] == names == [r[0] for r in _table(out, "cover")]
    assert out["hook"] == f"{n} companies reported quarterly results against analyst estimates."
    narrated = min(n, T.EARNINGS_NARRATED_COMPANIES)
    assert T.EARNINGS_NARRATED_COMPANIES == T.NEWS_SCRIPT_LINES - 1 == 3
    for i, name in enumerate(names[:narrated]):
        assert out["video_script"][i].startswith(T.spoken_company(name)), out["video_script"]
        assert names[i] in (out["cards"][i]["title"], out["cards"][i]["body"])
    tail = {2: [T.LEXICON["er.m.basis"], T.LEXICON["er.estimate"]], 3: [T.LEXICON["er.m.basis"]],
            4: [T.LEXICON["er.m.basis"]]}[n]
    assert out["video_script"][narrated:] == tail
    for f in T.EARNINGS_LIST_FIELDS:
        paras = out["captions"][f].split("\n\n")
        for r in rec.reports:
            assert sum(1 for p in paras if p.startswith(f"{r.company.name} ({r.company.symbol}): EPS of ")) == 1, (f, r)
    assert T.validate_package(out, record=rec) == []


def test_news_v23_revenue_always():
    """Owner 2026-11-19, "Revenue always" — the GE-type risk ACCEPTED (an adjusted consensus beside a GAAP figure):
    `EARNINGS_REVENUE_IN_POSTS` is on and EVERY company's revenue pair is written: its picture and cover Revenue
    line, its card, its narration line (unless the words run over: then its EPS line), every long caption's
    paragraph, the alt text. Mutation-checked by hand: the Revenue line dropped from `_earnings_parts` turned this
    red."""
    assert T.EARNINGS_REVENUE_IN_POSTS is True
    rec = earnings(others=2)
    out = compose(rec, EARNINGS_RUN)
    for r, (_name, _chip, lines) in zip(rec.reports, _table(out)):
        pair = T.revenue_pair(r.revenue_actual, r.revenue_estimate)
        assert lines[1][:3] == ["Revenue", pair["re_s"], pair["ra_s"]]
        assert any(c["body"].endswith(f"revenue {pair['ra_s']} vs {pair['re_s']}") for c in out["cards"])
        for f in T.EARNINGS_LIST_FIELDS:
            assert f"and revenue of {pair['ra']} vs an estimate of {pair['re']}." in out["captions"][f], f
        assert f"revenue {pair['ra_s']} vs {pair['re_s']}" in out["image_post"]["paragraphs"][1]


@pytest.mark.parametrize("ra, rv, image", [
    # a 0.09% shortfall printed "$1.1 billion vs $1.2 billion" (about 8%) when each was rounded alone
    (1_149_000_000.0, 1_150_000_000.0, ("$1.149B", "$1.150B")),
    (24_949_000_000.0, 24_950_000_000.0, ("$24.949B", "$24.950B")),
    (1_180_000_000.0, 1_150_000_000.0, ("$1.18B", "$1.15B")),
    (1_150_000_000.0, 1_150_000_000.0, ("$1.2B", "$1.2B")),                  # equal figures read equal
    (551_900_000.0, 543_600_000.0, ("$551.9M", "$543.6M")),
    (999_960_000.0, 990_000_000.0, ("$999.96M", "$990.00M")),                # the pair stays in millions
    (1_200_000_000.0, 900_000_000.0, ("$1.2B", "$0.9B")),                    # one unit, the larger figure's
    # fidelity review 2026-11-20: one VISIBLE precision — ".0" dropped only when both figures end in it
    (7_100_000_000.0, 7_000_000_000.0, ("$7.1B", "$7.0B")),
    (7_000_000_000.0, 7_100_000_000.0, ("$7.0B", "$7.1B")),
    (880_000_000.0, 860_000_000.0, ("$880M", "$860M")),
], ids=["shortfall_0.09pct", "shortfall_large_co", "gap_2.6pct", "equal", "golden", "rollup_edge", "one_unit",
        "one_place_beat", "one_place_short", "both_whole"])
def test_news_v23_the_revenue_pair_is_written_at_one_shared_precision(ra, rv, image):
    """Review round 9: the reported revenue and its estimate are ONE pair — the larger figure's unit, decimals grown
    from one to REVENUE_MAX_PLACES until the shown gap is within REVENUE_GAP_TOLERANCE of the true gap — on the
    picture's Revenue line, the cover, the card and the alt text alike."""
    out = compose(earnings(revenue_actual=ra, revenue_estimate=rv), EARNINGS_RUN)
    assert _table(out)[0][2][1][:3] == ["Revenue", image[1], image[0]]
    assert _table(out, "cover")[0][2][1][:3] == ["Revenue", image[1], image[0]]
    assert out["cards"][0]["body"].endswith(f"revenue {image[0]} vs {image[1]}")


@pytest.mark.parametrize("ra, rv, image, surprise", [
    (1_149_400_000.0, 1_150_000_000.0, ("$1.149B", "$1.150B"), f"{MINUS}0.1%"),   # a 0.05% gap
    (5_000_000_001.0, 5_000_000_000.0, ("$5.000B", "$5.000B"), "0.0%"),          # a one-dollar gap
], ids=["gap_0.05pct", "one_dollar"])
def test_news_v23_a_gap_no_precision_shows_is_written_at_the_finest_one(ra, rv, image, surprise):
    """"Revenue always": a pair whose gap no shared precision up to REVENUE_MAX_PLACES shows within the tolerance is
    no longer left out — both figures are written at REVENUE_MAX_PLACES (one unit, one precision, never each rounded
    alone) and the surprise, computed from the figures as filed, says how small the gap is."""
    assert T.revenue_pair(ra, rv) is None
    out = compose(earnings(revenue_actual=ra, revenue_estimate=rv), EARNINGS_RUN)
    assert _table(out)[0][2][1] == ["Revenue", image[1], image[0], surprise]


def test_news_v23_a_pair_the_post_cannot_write_drops_its_company():
    """A revenue pair under $1 million (the pair's smallest unit) cannot be written: the template refuses that company
    (`implausible_figures`) — a record holding it is refused whole at compose (forged), and `drop_refused_rows`
    drops it before the record is stored when the others still make a post."""
    small = earnings_row(company=co("TLSP", "Tailspin Toys"), revenue_actual=900_000.0, revenue_estimate=800_000.0)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(earnings(lead=small), EARNINGS_RUN)
    assert e.value.code == "implausible_figures" and "revenue" in e.value.detail
    narrowed = T.drop_refused_rows(earnings(lead=small, others=2), EARNINGS_RUN)
    assert [r.company.symbol for r in narrowed.reports] == ["CTSO", "LTW"]
    assert compose(narrowed, EARNINGS_RUN)["source_ref"] == "news:earnings:CTSO:2026-11-05+LTW:2026-11-05"


@pytest.mark.parametrize("a, e, eps, cell", [
    (4.86, 4.5, True, "+8.0%"), (0.62, 0.70, True, f"{MINUS}11.4%"), (-0.05, -0.12, True, "+58.3%"),
    (-0.12, -0.05, True, f"{MINUS}140.0%"), (1.12, 1.05, True, "+6.7%"), (3.2e9, 3.3e9, False, f"{MINUS}3.0%"),
    (12.4e9, 12.1e9, False, "+2.5%"), (1.0005, 1.0, True, "+0.1%"), (1.0004, 1.0, True, "0.0%"),
    (0.99951, 1.0, True, "0.0%"), (0.9995, 1.0, True, f"{MINUS}0.1%"), (4.5, 4.5, True, "0.0%"),
    (45.0, 4.5, True, "+900.0%"), (1234.0, 0.1, True, "+1,233,900.0%"),
    (0.5, 0.049, True, "—"), (0.5, -0.049, True, "—"), (0.5, 0.0, True, "—"),
    (0.5, 0.05, True, "+900.0%"), (5e8, 0.0, False, "—"), (5e8, -1.0, False, "—"),
], ids=["up", "down", "smaller_loss", "larger_loss", "nike", "rev_down", "rev_up", "half_up", "below_half",
        "below_half_neg", "half_up_neg", "equal", "nine_x", "comma", "eps_est_small", "eps_est_small_neg",
        "eps_est_zero", "eps_est_floor", "rev_est_zero", "rev_est_negative"])
def test_news_v23_the_surprise_cell(a, e, eps, cell):
    """(reported − estimate) / |estimate| × 100 on the figures as filed, ONE decimal, half-up away from zero; "+" or the
    real minus (U+2212); "0.0%" when it rounds to zero; "—" when no % is meaningful (|EPS est| under $0.05, a revenue
    estimate that is not positive). Every shape is one `_SURPRISE_CELL_RE` admits (the positional exemption)."""
    assert T.surprise_cell(a, e, eps=eps) == cell
    assert T._SURPRISE_CELL_RE.fullmatch(cell)
    assert T.EARNINGS_SURPRISE_MIN_ABS_EPS_ESTIMATE == Decimal("0.05")


def test_news_v23_the_dash_cell_stands_behind_the_estimate_gate():
    """The owner's "—" (|EPS est| < $0.05) never reaches a post today: the ten-cent gate (`EPS_MIN_ABS_ESTIMATE`,
    the adapter's `eps_estimate_too_small`, the record and the template) refuses such a company first — kept, never
    loosened (reported to the owner). A forged record carrying one is refused at compose."""
    assert R.EPS_MIN_ABS_ESTIMATE == 0.10 > float(T.EARNINGS_SURPRISE_MIN_ABS_EPS_ESTIMATE)
    with pytest.raises(ValueError):
        earnings_row(eps_estimate=-0.04)
    forged = earnings(lead=_unchecked(earnings_row(), eps_estimate=-0.04))
    with pytest.raises(T.NewsTemplateRefused) as err:
        compose(forged, EARNINGS_RUN)
    assert err.value.code == "implausible_figures"


@pytest.mark.parametrize("a, e, hook_line", [
    (4.86, 4.5, "Fabrikam reported $4.86 a share against an estimate of $4.50"),
    (-0.05, -0.12, "Fabrikam reported negative $0.05 a share against an estimate of negative $0.12"),
    (-0.05, 0.12, "Fabrikam reported negative $0.05 a share against an estimate of $0.12"),
    (0.31, -0.12, "Fabrikam reported $0.31 a share against an estimate of negative $0.12"),
    (0.0, -0.12, "Fabrikam reported $0.00 a share against an estimate of negative $0.12"),
], ids=["positive_positive", "negative_negative", "negative_vs_positive", "positive_vs_negative", "zero"])
def test_earnings_state_the_signed_figure_on_the_analysts_basis_never_a_profit_or_loss(a, e, hook_line):
    """Review round 9: no line says a company "earned" / "lost" / had "earnings of" — the figure is said with its sign:
    "negative $0.05" spoken, "-$0.05" in a caption, "−$0.05" (U+2212) in the picture's table; the X headline carries
    the basis note where it fits. Review 2026-11-20: the hook no longer says the figures are "on the analysts' basis"
    (the revenue estimate can be on another) — the narration's basis line says the estimate's basis can differ."""
    rec = earnings(company=co("FBKM", "Fabrikam"), eps_actual=a, eps_estimate=e)
    out = compose(rec, EARNINGS_RUN)
    # the line's long form, or its short one when the narration's words run over (`_pick_script`)
    short = hook_line.replace(" reported ", ": ").replace("against an estimate of", "against")
    assert out["video_script"][0].startswith((hook_line, short)), out["video_script"][0]
    assert "analysts' basis" not in out["hook"] and T.LEXICON["er.m.basis"] in out["video_script"]
    parts = _parts(rec, EARNINGS_RUN)
    own = {k: v for k, v in out.items() if k not in ("posts", "disclaimer_card")}
    for s in [*_strings(own), *_offered(rec, EARNINGS_RUN), *(v for g in parts.lines for v in g),
              *(t for c in parts.cards for t in c)]:
        assert not _PROFIT_LOSS_RE.search(s), s
    a_s, e_s = T.eps_image(a), T.eps_image(e)
    t = lambda s: MINUS + s[1:] if s.startswith("-") else s          # noqa: E731 — the table's minus
    assert _table(out)[0][2][0][:3] == ["EPS", t(e_s), t(a_s)]
    assert f"Fabrikam (FBKM): EPS of {a_s} vs an analyst estimate of {e_s}" in out["captions"]["linkedin"]
    assert "basis can differ" in out["captions"]["x"].split("\n\n")[0]
    assert "-" not in " ".join([out["hook"], *out["video_script"]])         # narration: magnitudes in words
    assert not T.EARNINGS_BANNED_RE.search(fold(json.dumps({k: v for k, v in out.items()
                                                            if k not in ("image_spec", "opening_card")})))


@pytest.mark.parametrize("rec, run_date, code", [
    (earnings(), date(2026, 11, 13), "stale_source"),                     # reported 8 days before the run
    (earnings(), date(2026, 11, 5), "stale_source"),                      # reported on the run day
    (earnings(eps_actual=45.0, eps_estimate=4.5), EARNINGS_RUN, "implausible_figures"),          # a digit shift
    (earnings(eps_actual=-5.0, eps_estimate=0.4), EARNINGS_RUN, "implausible_figures"),          # gap > 10x
    (earnings(eps_actual=0.004, eps_estimate=0.12), EARNINGS_RUN, "implausible_figures"),        # reads $0.00
    (earnings(lead=_unchecked(earnings_row(), eps_estimate=0.05)), EARNINGS_RUN, "implausible_figures"),
    (earnings(period_end=date(2026, 7, 7)), EARNINGS_RUN, "implausible_figures"),              # 121 days
    (earnings(lead=_unchecked(earnings_row(), revenue_actual=900e6)), EARNINGS_RUN, "implausible_figures"),
    (earnings(lead=_unchecked(earnings_row(), revenue_estimate=None)), EARNINGS_RUN, "implausible_figures"),
    (earnings(company=_unchecked(co("NWFT", "Northwind"), name="Shocking Fitness")), EARNINGS_RUN, "slot_rejected"),
    # news-v19: a VALID record without a revenue pair (the record class still admits one) is refused — the pair
    # is the only check that the row is the quarter the estimate is for (Progressive's one-month row)
    (earnings(revenue_actual=None, revenue_estimate=None), EARNINGS_RUN, "implausible_figures"),
    (earnings(revenue_actual=None, revenue_estimate=None, period_end=None), EARNINGS_RUN, "implausible_figures"),
    # review of 2026-10-16 (ER-1): figures that PRINT as one say nothing, whatever gap the feed's figures had
    (earnings(eps_actual=0.095, eps_estimate=0.10), EARNINGS_RUN, "implausible_figures"),
    (earnings(eps_actual=0.1449, eps_estimate=0.1376), EARNINGS_RUN, "implausible_figures"),
    (earnings(eps_actual=-0.095, eps_estimate=-0.10), EARNINGS_RUN, "implausible_figures"),
    (earnings(eps_actual=4.504, eps_estimate=4.5), EARNINGS_RUN, "implausible_figures"),
    # the owner-delegated EPS ceiling (2026-10-19)
    (earnings(lead=_unchecked(earnings_row(), eps_actual=1_500_000.0, eps_estimate=1_400_000.0)), EARNINGS_RUN,
     "implausible_figures"),
    (earnings(lead=_unchecked(earnings_row(), eps_estimate=1_000_000.5)), EARNINGS_RUN, "implausible_figures"),
], ids=["old", "same_day", "digit_shift", "gap", "sub_cent", "tiny_estimate", "period_old", "revenue_band",
        "revenue_one_sided", "slot_word", "no_revenue", "no_revenue_no_period", "prints_equal_0.10",
        "prints_equal_0.14", "prints_equal_negative", "prints_equal_4.50", "eps_above_ceiling",
        "estimate_above_ceiling"])
def test_earnings_refusals(rec, run_date, code):
    """Every per-company gate, on the LEAD company of a two-company record: a record that reaches `compose` with a
    refused company is forged and refused whole (the script flow drops such a company first: `drop_refused_rows`)."""
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == code


@pytest.mark.parametrize("rec, run_date, code", [
    (earnings(eps_actual=45.0, eps_estimate=4.5), EARNINGS_RUN, "implausible_figures"),
    (earnings(eps_actual=0.095, eps_estimate=0.10), EARNINGS_RUN, "implausible_figures"),
    (earnings(lead=_unchecked(earnings_row(), revenue_estimate=None)), EARNINGS_RUN, "implausible_figures"),
    (earnings(company=_unchecked(co("NWFT", "Northwind"), name="Shocking Fitness")), EARNINGS_RUN, "slot_rejected"),
    (earnings(period_end=date(2026, 7, 7)), EARNINGS_RUN, "implausible_figures"),
], ids=["digit_shift", "prints_equal", "revenue_one_sided", "slot_word", "period_old"])
def test_news_v23_a_refused_company_is_dropped_never_the_post(rec, run_date, code):
    """"A refused company is dropped from the post, not the post" (owner): `drop_refused_rows` removes the refused
    LEAD (its WARNING names it and the gate) and keeps the rest in order — so with two others left the post composes
    with them, its ledger key naming exactly those; with one left the record is refused `too_few_rows` (the chain
    falls through). A record whose every company passes comes back as it is."""
    three = earnings(lead=rec.reports[0], others=2)
    narrowed = T.drop_refused_rows(three, run_date)
    assert [r.company.symbol for r in narrowed.reports] == ["CTSO", "LTW"]
    out = compose(narrowed, run_date)
    assert out["source_ref"] == f"news:earnings:CTSO:{rec.reports[0].report_date}+LTW:{rec.reports[0].report_date}"
    with pytest.raises(T.NewsTemplateRefused) as e:
        T.drop_refused_rows(rec, run_date)
    assert e.value.code == "too_few_rows"
    ok = earnings(others=2)
    assert T.drop_refused_rows(ok, EARNINGS_RUN) is ok
    assert T.drop_refused_rows(congress(), CONGRESS_RUN) is not None
    for other in (costco(), berkshire()):                           # every other series: as it is
        assert T.drop_refused_rows(other, EARNINGS_RUN) is other


def test_news_v23_a_refused_stake_is_dropped_never_the_post(caplog):
    """company stakes (news-v23): a stake its template refuses (a person in its source title, a word it cannot draw,
    a stale figure) is DROPPED by `drop_refused_rows` — the WARNING names its id and symbol, never its text — and the
    post composes with the rest, its ledger key naming exactly those; one stake left is still a post (1-4); none
    left refuses the post with the stake's own code. A record whose every stake passes comes back as it is. Mutation-checked by hand
    (2026-11-20): the `CompanyStakes` branch removed from `drop_refused_rows`, the narrowed asserts turned red."""
    bad = replace(lumentum_stake(), source_title="Jensen Huang keynote (Mar 18, 2026)")
    rec = stakes(intel_stake(), synopsys_stake(), bad)
    caplog.set_level(logging.WARNING, logger=T.__name__)
    narrowed = T.drop_refused_rows(rec, STAKES_RUN)
    assert [r.investee.symbol for r in narrowed.stakes] == ["INTC", "SNPS"]
    assert "stake 6d8f0a2c-4e6a-4c8e-9a1c-3e5a7c9e1b08 (LITE) DROPPED from the post" in caplog.text
    assert "Jensen" not in caplog.text and "Huang" not in caplog.text
    out = compose(narrowed, STAKES_RUN)
    assert out["source_ref"] == f"news:company_stakes:{intel_stake().stake_id}+{synopsys_stake().stake_id}"
    lone = T.drop_refused_rows(stakes(intel_stake(), bad), STAKES_RUN)
    assert [r.investee.symbol for r in lone.stakes] == ["INTC"]
    # every stake refused: the post is refused with the (first) stake's OWN reason — never a bare "too few"
    with pytest.raises(T.NewsTemplateRefused) as e:
        T.drop_refused_rows(stakes(bad), STAKES_RUN)
    assert e.value.code == "slot_rejected"
    ok = nvidia_stakes()
    assert T.drop_refused_rows(ok, STAKES_RUN) is ok
    with pytest.raises(T.NewsTemplateRefused) as e:          # a forged record with the refused stake is refused whole
        compose(rec, STAKES_RUN)
    assert e.value.code == "slot_rejected"


@pytest.mark.parametrize("a, e", [(0.095, 0.10), (0.1449, 0.1376), (-0.095, -0.10), (4.504, 4.5), (-4.5049, -4.5)])
def test_earnings_figures_that_print_as_one_are_refused_by_that_rule(a, e):
    """ER-1: the refusal comes from the printed-figure rule itself (not a neighbouring check), and one cent apart
    still composes."""
    with pytest.raises(T.NewsTemplateRefused) as err:
        compose(earnings(company=co("FBKM", "Fabrikam"), eps_actual=a, eps_estimate=e), EARNINGS_RUN)
    assert err.value.code == "implausible_figures" and "print as the same figure" in err.value.detail
    out = compose(earnings(company=co("FBKM", "Fabrikam"), eps_actual=0.11, eps_estimate=0.10), EARNINGS_RUN)
    assert _table(out)[0][2][0] == ["EPS", "$0.10", "$0.11", "+10.0%"]


@pytest.mark.parametrize("word", ["beat", "missed", "expected", "surprise", "surprised", "topped", "exceeded",
                                  "fell short", "disappointing", "strong", "weak", "green", "red", "better", "worse",
                                  "crushed"])
def test_earnings_never_puts_a_verdict_beside_the_figures(word):
    """Every verdict word is refused in the narration and the captions — "surprise" / "surprised" included: the
    table's column LABEL is the one exemption (`test_news_v23_the_surprise_exemptions_are_positional`)."""
    for mutate in (lambda o: o.update(hook=o["hook"].replace("results", f"{word} results")),
                   lambda o: o["captions"].update(x=o["captions"]["x"] + f" A {word} result.")):
        out = compose(earnings(), EARNINGS_RUN)
        mutate(out)
        assert {"series_word", "banned_word"} & _codes(out, earnings()), word


def _set(o, path, value):
    """Set `value` at a dotted / indexed path of `o` ("image_spec.sections[0].columns[2]")."""
    parts = re.findall(r"[a-z_]+|\[[0-9]+\]", path)
    cur = o
    for p in parts[:-1]:
        cur = cur[int(p[1:-1])] if p.startswith("[") else cur[p]
    last = parts[-1]
    if last.startswith("["):
        cur[int(last[1:-1])] = value
    else:
        cur[last] = value


_SURPRISE_CELL = "image_spec.sections[0].rows[0].table[0].cells[3]"
_COVER_SURPRISE_CELL = "opening_card.list.rows[1].table[1].cells[3]"


def test_news_v23_the_surprise_exemptions_are_positional():
    """The two exemptions the owner's table needs, POSITIONAL (marketing.md §7), each with its must-reject twins:
    the surprise CELL ("+8.0%" — the last cell of a table line, on the picture or the cover) is the one other "%" that
    stands alone, and the column LABEL "Surprise" (the table's last column) the one string EARNINGS_BANNED_RE's
    `surpris\w*` lets stand. Mutation-checked by hand: with either field pattern widened to every path, the twins
    below composed clean (red); with either exemption removed, the clean sample refused (red)."""
    rec = earnings()
    out = compose(rec, EARNINGS_RUN)
    assert _codes(out, rec) == set()
    # twins — the cell shape anywhere else, another string in the cell, the label anywhere else or another label
    for path, value, code in (
        ("captions.x", out["captions"]["x"] + " +8.0%", "structure"),
        ("image_spec.title", "Reported +8.0%", "structure"),
        ("image_spec.sections[0].rows[0].table[0].cells[1]", "+8.0%", "structure"),       # the estimate cell
        (_SURPRISE_CELL, "8%", "structure"),                                               # not its shape
        (_SURPRISE_CELL, "+8.0% beat", "structure"),
        (_COVER_SURPRISE_CELL, "+3.0% gain", "structure"),
        ("captions.linkedin", out["captions"]["linkedin"] + " Surprise.", "series_word"),
        ("hook", out["hook"].replace("results", "surprise results"), "series_word"),
        ("image_spec.title", "Surprise", "series_word"),
        ("image_spec.sections[0].columns[1]", "Surprise", "series_word"),
        ("image_spec.sections[0].columns[2]", "Surprised", "series_word"),
        ("opening_card.list.columns[2]", "Surprise result", "series_word"),
    ):
        o = copy.deepcopy(out)
        _set(o, path, value)
        found = {(v["field"], v["code"]) for v in T.validate_package(o, record=rec)}
        field_ = path if not path.startswith("captions.") else path
        assert (field_, code) in found, (path, value, found)
    # the exemptions are the EARNINGS series' only
    assert not T._SURPRISE_COLUMN_FIELD_RE.fullmatch("image_spec.sections[0].rows[0].cells[2]")
    assert T._SURPRISE_CELL_FIELD_RE.fullmatch(_SURPRISE_CELL) and T._SURPRISE_CELL_FIELD_RE.fullmatch(
        _COVER_SURPRISE_CELL)


def test_news_v23_breaking_needs_every_company_fresh(monkeypatch):
    """The first-line prefix (news-v22) says BREAKING on an earnings post only when EVERY company's report is fresh
    (at most one trading session old): one older report among them and the post carries no prefix."""
    fresh = earnings(report_date=date(2026, 11, 10))           # Tue; the run Wed Nov 11
    assert T._breaking_fresh(fresh, date(2026, 11, 11))
    mixed = earnings_day([earnings_row(report_date=date(2026, 11, 10)),
                          R.EarningsReport(series="earnings", report_date=date(2026, 11, 6),
                                           period_end=date(2026, 9, 30), **EARNINGS_OTHERS[0])])
    assert not T._breaking_fresh(mixed, date(2026, 11, 11))
    assert compose_on(mixed, date(2026, 11, 11))["first_line_prefix"] is None
    assert compose_on(fresh, date(2026, 11, 11))["first_line_prefix"] == BREAKING


def _figure(text: str) -> float:
    """"$1.149 billion" / "$1.149B" → 1.149e9 (the pair's own two forms)."""
    m = re.fullmatch(r"\$([0-9,]+(?:\.[0-9]+)?)\s?(M|B|T|million|billion|trillion)", text)
    return float(m.group(1).replace(",", "")) * {"M": 1e6, "B": 1e9, "T": 1e12, "million": 1e6, "billion": 1e9,
                                                  "trillion": 1e12}[m.group(2)]


def test_a_shown_revenue_pair_always_keeps_the_true_gap_sign_and_size():
    """Seeded property sweep (4,000 pairs inside the revenue band, most of them near-matches): a pair
    that `revenue_pair` writes keeps the true gap's sign, reads equal only when the figures are equal, uses one
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
            assert places[0] == places[1], pair            # one VISIBLE precision (fidelity review 2026-11-20)
    assert shown > 3000


def test_news_v23_the_surprise_sign_never_contradicts_the_written_pair():
    """Seeded sweep over the WRITTEN pairs (`_revenue_figures`, the finest-precision fallback included): the surprise
    cell's sign is the true gap's, and "0.0%" only when the true gap is under 0.05% of the estimate — a pair that reads
    equal never carries a non-zero surprise of the other sign."""
    rnd = random.Random(20261119)
    for _ in range(3000):
        est = 10 ** rnd.uniform(6.2, 12.5)
        act = est * (1 + rnd.choice((rnd.uniform(-0.002, 0.002), rnd.uniform(-0.05, 0.05))))
        figs = T._revenue_figures(act, est)
        cell = T.surprise_cell(act, est, eps=False)
        gap = _figure(figs["ra_s"]) - _figure(figs["re_s"])
        if cell.startswith("+"):
            assert act > est and gap >= 0, (act, est, figs, cell)
        elif cell.startswith(MINUS):
            assert act < est and gap <= 0, (act, est, figs, cell)
        else:
            assert cell == "0.0%" and abs(act - est) / est < 0.0005 + 1e-12, (act, est, cell)


def test_no_article_stands_before_an_earnings_figure():
    """An "a" before a figure would depend on how it is read ("an $8.1B estimate"): every earnings string says "an
    analyst estimate of {figure}" / "an estimate of {figure}" instead."""
    for factory in (earnings, earnings_profit, lambda: earnings(others=3),
                    lambda: earnings(eps_actual=8.1, eps_estimate=7.05, revenue_actual=18e9, revenue_estimate=18.2e9)):
        out = compose(factory(), EARNINGS_RUN)
        for s in _strings(out):
            assert not re.search(r"\b[Aa]n?\s+[-−]?\$", s), s


#: GE Aerospace's Q3 2025 report (FMP's calendar, the read-only probe of 2026-10-20): EPS $1.66 against $1.46, and
#: revenue $12.221B — the GAAP total — against a $10.385B estimate, the consensus for its ADJUSTED revenue. news-v21 left
#: every revenue pair out for this; news-v23 ("Revenue always", owner 2026-11-19) prints it, the risk ACCEPTED.
GE_RUN = date(2025, 10, 23)        # Thursday; reports Oct 16 .. 22


def ge_aerospace_2025(**kw):
    base = dict(series="earnings", company=co("GE", "GE Aerospace"), report_date=date(2025, 10, 21),
                period_end=date(2025, 9, 30), eps_actual=1.66, eps_estimate=1.46, revenue_actual=12.221e9,
                revenue_estimate=10.385e9)
    base.update(kw)
    return R.EarningsReport(**base)


def test_news_v23_ge_aerospaces_two_basis_quarter_prints_its_revenue_the_risk_accepted():
    """The counterexample that made news-v21 leave revenue out, composed under the owner's "Revenue always": GE
    Aerospace's revenue pair is printed ($12.2B vs an estimate of $10.4B, +17.7%), beside the picture's note and the
    captions' basis paragraph — the risk the owner ACCEPTED, recorded on `EARNINGS_REVENUE_IN_POSTS`."""
    travelers = ge_aerospace_2025(company=co("TRV", "Travelers"), report_date=date(2025, 10, 16), eps_actual=8.14,
                                  eps_estimate=6.39, revenue_actual=12.470e9, revenue_estimate=11.688e9)
    rec = earnings_day([ge_aerospace_2025(), travelers])
    out = compose(rec, GE_RUN)
    assert _table(out)[0] == ("GE Aerospace", "GE", [["EPS", "$1.46", "$1.66", "+13.7%"],
                                                     ["Revenue", "$10.4B", "$12.2B", "+17.7%"]])
    assert out["image_spec"]["notes"] == [T.LEXICON["er.img.note"]]
    assert "and revenue of $12.2 billion vs an estimate of $10.4 billion." in out["captions"]["linkedin"]
    assert T.LEXICON["er.m.basis"] in out["captions"]["linkedin"] or "basis can differ" in out["captions"]["linkedin"]
    assert "GE-type risk" in inspect.getsource(T).split("EARNINGS_REVENUE_IN_POSTS = True")[0][-2400:]


#: A basis caveat that covers EVERY figure of an earnings post — never one narrowed to the EPS (review 2026-11-20).
_GENERAL_BASIS_RE = re.compile(r"(?:an estimate's basis can differ|a reported figure can be on a different basis|"
                               r"(?:its|the consensus) basis can differ)", re.I)
_EPS_ONLY_BASIS_RE = re.compile(r"\bEPS basis\b", re.I)


@pytest.mark.parametrize("n", [2, 3, 4])
@pytest.mark.parametrize("lead", ["northwind", "ge"])
def test_every_earnings_video_carries_a_basis_caveat_and_no_hook_claims_the_analysts_basis(n, lead):
    """Review 2026-11-20 (confirmed): since news-v23 the results include the revenue pair, whose estimate can be on
    another basis than the reported figure (GE Aerospace: adjusted consensus vs GAAP — the risk the owner accepted).
    So (a) no hook may say the results are "on the analysts' basis", and (b) EVERY earnings video — 2, 3 or 4
    companies — narrates the basis note and shows its "About the figures" card: a four-company video used to fill all
    four lines with companies and carry the caveat on no frame and in no narration (the cover's list has no note).
    Mutation-checked by hand: restoring `er.m.hook.basis` as the first hook, or narrating every company
    (`rows_[:EARNINGS_NARRATED_COMPANIES]` → `rows_`), turns this red."""
    if lead == "ge":
        others = [ge_aerospace_2025(company=co(sym, name), report_date=date(2025, 10, 16), eps_actual=e,
                                    eps_estimate=e * 0.8, revenue_actual=r * 1.1, revenue_estimate=r)
                  for sym, name, e, r in (("TRV", "Travelers", 8.14, 11.688e9), ("LTW", "Litware", 2.10, 4.0e9),
                                          ("ADWK", "Adventure Works", 1.40, 2.5e9))][:n - 1]
        rec, day = earnings_day([ge_aerospace_2025(), *others]), GE_RUN
    else:
        rec, day = earnings(others=n - 1), EARNINGS_RUN
    out = compose(rec, day)
    assert len(rec.reports) == n
    spoken = [out["hook"], *out["video_script"]]
    assert not any("analysts' basis" in line for line in spoken), spoken
    assert T.LEXICON["er.m.basis"] in out["video_script"], out["video_script"]
    assert {"title": T.LEXICON["er.c3.title"], "body": T.LEXICON["er.c3.body"]} in out["cards"]
    for line in spoken:
        assert not _EPS_ONLY_BASIS_RE.search(line), line


@pytest.mark.parametrize("n", [2, 3, 4])
def test_every_caption_that_prints_revenue_carries_a_basis_caveat_that_is_not_eps_only(n):
    """Review 2026-11-20 (confirmed): the earnings captions print each company's revenue pair, and their first line
    (`er.m.h.names.basis` / `er.m.h.basis`) used to say only "the EPS basis can differ" — while it stood in for the
    body's general basis paragraph (`stated_by_headline`), so no long caption said the REVENUE estimate's basis can
    differ too. Every caption carrying a revenue figure now carries a caveat covering every figure, and none narrows it
    to the EPS. Mutation-checked by hand: the old "the EPS basis can differ" wording turns this red."""
    for rec, day in ((earnings(others=n - 1), EARNINGS_RUN),
                     (earnings_day([ge_aerospace_2025(), *[ge_aerospace_2025(
                         company=co(sym, name), report_date=date(2025, 10, 16)) for sym, name in
                         (("TRV", "Travelers"), ("LTW", "Litware"), ("ADWK", "Adventure Works"))][:n - 1]]), GE_RUN)):
        out = compose(rec, day)
        printed = 0
        for f, caption in out["captions"].items():
            assert not _EPS_ONLY_BASIS_RE.search(caption), (f, caption)
            if "and revenue of " in caption or "revenue " in caption.split("\n\n", 1)[-1]:
                printed += 1
                assert _GENERAL_BASIS_RE.search(caption), (f, caption)
        assert printed >= len(T.EARNINGS_LIST_FIELDS), out["captions"].keys()


@pytest.mark.parametrize("rec", [
    earnings(revenue_actual=None, revenue_estimate=None),
    earnings(revenue_actual=None, revenue_estimate=None, period_end=None),
    earnings(lead=_unchecked(earnings_row(), revenue_estimate=None)),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=None)),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=499_999_999.0, revenue_estimate=1_000_000_000.0)),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=1_500_000_001.0, revenue_estimate=1_000_000_000.0)),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=0.0)),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=-551_900_000.0)),
    earnings(lead=_unchecked(earnings_row(), revenue_estimate=0.0)),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=float("nan"))),
    earnings(lead=_unchecked(earnings_row(), revenue_actual=float("inf"))),
], ids=["no_pair", "no_pair_no_period", "estimate_only_missing", "actual_only_missing", "below_band", "above_band",
        "zero_actual", "negative_actual", "zero_estimate", "nan_actual", "inf_actual"])
def test_the_revenue_pair_is_still_required(rec):
    """The revenue pair stays the data-quality and quarter check (news-v19): a company without one, or with one
    outside `REVENUE_RATIO_BAND` (or not a positive finite pair), is refused `implausible_figures` — and now dropped
    from its post (`drop_refused_rows`), never shown without its revenue."""
    with pytest.raises(T.NewsTemplateRefused) as err:
        compose(rec, EARNINGS_RUN)
    assert err.value.code == "implausible_figures" and "revenue" in err.value.detail, err.value.detail
    with pytest.raises(T.NewsTemplateRefused) as err:
        T.drop_refused_rows(rec, EARNINGS_RUN)
    assert err.value.code == "too_few_rows"


# ── Theme Explainer ───────────────────────────────────────────────────────────


def _members(n, *, name=lambda i: f"Company {i:02d}", seg=lambda i: "Cloud", share=lambda i: 0.5):
    syms = [f"{a}{b}" for a in "ABCDEFGHJK" for b in "ABCDEFGHJK"][:n]
    return [(syms[i], name(i), seg(i), share(i), "2026") for i in range(n)]


def test_theme_lists_every_member_in_the_long_captions_and_draws_twelve():
    """news-v23 phase 3 (the "+N more" promises of the 13F list, RESTYLE_SPEC §3): every caption that rides the picture
    lists the companies the picture leaves out, every caption that rides the video those the cover leaves out — each
    with its figure — and an outlet that cannot keep its promise is DROPPED (`over_budget`), never posted without it.
    A realistic large theme (24 members, half with a revenue-line fact: the picture draws the fact members first)
    keeps every outlet; a theme of 24 members ALL with facts is too long for TikTok, Threads, Instagram and the YouTube
    description, which are dropped, while the outlets that remain still list what they owe."""
    rows = _members(24, seg=lambda i: "Cloud" if i % 2 == 0 else None, share=lambda i: 0.5 if i % 2 == 0 else None)
    out = compose(theme(rows, theme_size=24), THEME_RUN)
    names = [r[1] for r in rows]
    facts = names[0::2]                               # drawn first (12 tiles), the 12 without a fact after them
    assert [t["name"] for t in out["image_spec"]["tiles"]] == facts
    assert len(out["image_spec"]["tiles"]) == 12 and out["image_spec"]["more"] == "+12 more"
    assert out["dropped_outlets"] == {} and sorted(out["posts"]) == sorted(post_copy.PLATFORMS)
    for f in ("instagram", "youtube_description", "facebook", "linkedin"):
        assert all(n in out["captions"][f] for n in names), f
    for f in ("threads", "x", "bluesky"):                          # the picture's twelve left out, each listed
        if f in out["text_outlets"]:
            continue
        assert all(n in out["captions"][f] for n in names[1::2]), f
    assert all(n in out["captions"]["tiktok"] for n in names[1::2] + facts[4:])     # the cover's twenty
    # news-v23 phase 3 (theme_ai_chips.jpg): the sub line explains the tiles and says the fiscal year they draw
    assert out["image_spec"]["subtitle"] == ("24 companies · each company's largest business and its share of total "
                                             "revenue · fiscal 2026")
    assert out["hook"] == "Where do 24 companies in AI chips get their revenue?"
    assert out["opening_card"]["figure"] == "24" and out["opening_card"]["logos"] == [rows[0][0]]
    # the cover is the list (the 13F cover's idea): its first four companies, "+20 more"
    assert [r["cells"][0] for r in out["opening_card"]["list"]["rows"]] == facts[:4]
    assert out["opening_card"]["list"]["more"] == "+20 more"
    narrated = [n for n in names if n in " ".join(out["video_script"])]
    assert 1 <= len(narrated) <= T.THEME_NARRATED_MAX
    assert out["image_post"]["paragraphs"][0].startswith("The image shows 12 of the 24 companies")
    # every one of 24 with a fact: the video's and Threads' promises cannot fit — those outlets are dropped, never
    # posted beside a "+N more" they do not keep; the rest keep theirs
    full = compose(theme(_members(24), theme_size=24), THEME_RUN)
    assert set(full["dropped_outlets"]) == {"tiktok", "threads", "instagram", "youtube"}
    assert all(v[0]["code"] == "over_budget" for v in full["dropped_outlets"].values())
    for f in ("facebook", "linkedin"):
        assert all(f"Company {i:02d}: Cloud, 50% of fiscal 2026 revenue." in full["captions"][f] for i in range(12, 24))


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
    assert (out["opening_card"]["figure"], out["opening_card"]["headline"]) == ("14", "AI chips")
    assert out["image_spec"]["subtitle"] == ("14 of its 16 companies · each company's largest business and its share of "
                                             "total revenue · fiscal 2025 or 2026")
    assert out["opening_card"]["list"]["heading"] == out["image_spec"]["subtitle"]
    assert out["image_spec"]["more"] == "+2 more"          # the LISTED members not drawn, never the gated ones
    assert out["image_post"]["paragraphs"][0].startswith("The image shows 12 of the 16 companies in AI chips.")
    for f, body in out["captions"].items():
        assert "14 of its 16 companies" in body.split("\n\n")[0], f
    for s in [*_strings(out), *_offered(ai_chips_of_more())]:
        assert not _WHOLE_14_RE.search(s), s
    # a seven-word theme: the longer hooks run past HOOK_WORDS, the tight one still gives "of its"
    title = "Space and Satellite Launch and Service Providers"
    out = compose(theme(title=title, theme_size=16), THEME_RUN)
    assert out["hook"] == f"{title}: revenue at 14 of its 16 companies."
    assert compose(theme(title=title, theme_size=14), THEME_RUN)["hook"] == (
        f"{title}: where 14 companies get their revenue.")


@pytest.mark.parametrize("size, members_line, whole", [
    (14, "\n\nNVIDIA: Data Center, 88% of fiscal 2026 revenue.", True),     # the record says the list is the theme
    (None, "\n\nNVIDIA: Data Center, 88% of fiscal 2026 revenue.", False),  # size not recorded: no size claim at all
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
        + " It gives each one's largest revenue line and its share of revenue in fiscal 2026.")


def test_theme_says_each_ones_segment_only_when_every_member_has_one():
    """"by each one's largest reported segment" (the caption headline) only when every LISTED member has
    its fact in the post, and the alt text's "each one's" only when every drawn tile carries its segment
    (else how many do). Mutation-checked (2026-10-10, in memory): with ".each" always chosen, the AI
    chips lines turned red; with it never chosen, the all-facts lines did."""
    for rec in (ai_chips(), ai_chips_of_more(), theme()):       # Arm without a share, KLA none
        out = compose(rec, THEME_RUN)
        assert not any("each one's" in s for s in [*_strings(out), *_offered(rec)]), rec.theme_size
        # news-v23 phase 3: Intel's fiscal 2022 fact is drawn TAGGED, Synopsys' by its short label — 11 tiles of 12
        # carry a share (Arm's line is its segment alone); the alt text says so, and that one line is older
        assert out["image_post"]["paragraphs"][0].endswith(
            " It gives the largest revenue line and its share of revenue in fiscal 2025 or 2026 for 11 of them. A line "
            "from an earlier fiscal year is marked with its year.")
    out = compose(theme(_members(8), theme_size=8), THEME_RUN)
    assert out["captions"]["facebook"].startswith(
        "AI chips: where 8 companies get their revenue, by each one's largest revenue line.")
    out = compose(theme(_members(8), theme_size=10), THEME_RUN)
    assert out["captions"]["facebook"].startswith("AI chips: where 8 of its 10 companies get their revenue, by each "
                                                  "one's largest revenue line.")
    # news-v23 phase 3: the members WITH a fact are drawn first — three facts past the twelfth member lead the grid
    rows = _members(15, seg=lambda i: "Cloud" if i >= 12 else None, share=lambda i: 0.5 if i >= 12 else None)
    out = compose(theme(rows, theme_size=15), THEME_RUN)
    assert [t["name"] for t in out["image_spec"]["tiles"]][:3] == ["Company 12", "Company 13", "Company 14"]
    assert [t.get("line") for t in out["image_spec"]["tiles"]][:4] == ["Cloud · 50% of revenue"] * 3 + [None]
    assert out["image_post"]["paragraphs"][0] == (
        "The image shows 12 of the 15 companies in AI chips. It gives the largest revenue line and its share of revenue "
        "in fiscal 2026 for 3 of them.")


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
    # news-v23 phase 3 (owner 2026-11-19: "for AI CHIPS, i don't see ARM, Intel and SNPS? add for me"): Intel's fiscal
    # 2022 fact is drawn with its OWN year, never implied current; Synopsys' filed name by its code-owned short label
    assert tiles["Intel"] == "Client Computing, FY2022 · 55% of revenue"
    assert tiles["Synopsys"] == "EDA Software · 81% of revenue"
    assert tiles["Arm Holdings"] == "Royalty"          # no share: the segment alone
    assert tiles["NVIDIA"] == "Data Center · 88% of revenue"
    assert "KLA" not in tiles and "Lam Research" not in tiles                  # 13th (a fact) and no fact: "+2 more"
    linkedin = out["captions"]["linkedin"]
    # the caption keeps the FULL filed name and every fact's own year; the short label is the picture's
    assert "KLA is also in AI chips." in linkedin
    assert "Intel: Client Computing, 55% of fiscal 2022 revenue." in linkedin
    assert "Synopsys: Electronic Design Automation Software, 81% of fiscal 2025 revenue." in linkedin
    assert "EDA Software" not in linkedin
    # a name that wraps past two tile lines at the floor is left off the grid, counted in "+k more", still listed
    long_name = "Wwwwwwwwww Wwwwwwwwww Wwwwwwwwww"            # three lines at the floor: no two words share one
    rows = _members(8)
    rows[1] = (rows[1][0], long_name, "Cloud", 0.5, "2026")
    out = compose(theme(rows), THEME_RUN)
    assert long_name not in [t["name"] for t in out["image_spec"]["tiles"]]
    assert out["image_spec"]["more"] == "+1 more" and long_name in out["captions"]["linkedin"]
    # a share that would push its tile past THEME_TILE_MAX_LINES is left off the tile only (the segment alone)
    rows = _members(8, seg=lambda i: "Wwwwwwwwwwwwwwww Wwwwwwwwwwwwwwww", share=lambda i: 0.995)
    out = compose(theme(rows), THEME_RUN)
    assert out["image_spec"]["tiles"][0]["line"] == "Wwwwwwwwwwwwwwww Wwwwwwwwwwwwwwww"
    assert "more than 99% of fiscal 2026 revenue" in out["captions"]["linkedin"]


@pytest.mark.parametrize("rec, run_date, code", [
    (ai_chips(), date(2027, 1, 11), "stale_source"),                      # 71 days after the member list
    (theme(tickers_as_of=date(2026, 11, 17)), THEME_RUN, "stale_source"),
    (theme(_members(8, seg=lambda i: None if i > 1 else "Cloud", share=lambda i: None)), THEME_RUN, "too_few_rows"),
    (theme(title="Supercalifragilistics Robotics"), THEME_RUN, "slot_rejected"),    # a 21-char word
    (theme(title="Followers of AI"), THEME_RUN, "slot_rejected"),
    (theme(title="Semiconductors Biotechnology Infrastructure Pharmaceutical"), THEME_RUN, "slot_rejected"),
    # news-v23 phase 3: a word no tile column holds at the floor (20 characters, measured with the worker's font)
    (theme(_members(8, name=lambda i: f"Wwwwwwwwwwwwwwwwwwww Co{i}")), THEME_RUN, "slot_rejected"),   # no tile fits
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
    # news-v16: only facts of ONE fiscal year share a line, and the line says it — NVIDIA (fiscal 2026) is
    # narrated alone, Broadcom and AMD (both fiscal 2025) as a pair; the cards keep the line's year
    assert out["video_script"][:2] == ["Nvidia's largest revenue line in fiscal 2026 was Data Center.",
                                       "Broadcom's largest revenue line in fiscal 2025 was Semiconductor Solutions, "
                                       "and AMD's was Data Center."]
    assert out["cards"][:2] == [{"title": "NVIDIA", "body": "Largest revenue line in fiscal 2026: Data Center"},
                                {"title": "Broadcom and AMD", "body": "Fiscal 2025: Semiconductor Solutions · Data Center"}]
    # four-word names: a pair no longer fits 20 words, so each fact gets its own line
    long = _members(6, name=lambda i: f"Northwind Data Systems {i:02d}", seg=lambda i: "Cloud and Data Infrastructure")
    out = compose(theme(long), THEME_RUN)
    assert out["video_script"][0] == ("Northwind Data Systems 00's largest revenue line in fiscal 2026 "
                                      "was Cloud and Data Infrastructure.")
    assert out["cards"][0] == {"title": "Northwind Data Systems 00",
                               "body": "Largest revenue line in fiscal 2026: Cloud and Data Infrastructure"}
    # a member name word too long for a card TITLE (CARD_TITLE_WORD_MAX_CHARS): the generic title,
    # the names in the body
    wide = _members(6, name=lambda i: f"Northwindcorporation {i:02d}", seg=lambda i: "Cloud") + [
        (f"S{a}", f"Fabrikam {a}", "Cloud", 0.5, "2026") for a in "ABC"]          # the grid draws these
    out = compose(theme(wide), THEME_RUN)
    assert out["cards"][0] == {"title": "Largest revenue lines",
                               "body": "Fiscal 2026 · Northwindcorporation 00: Cloud · Northwindcorporation 01: Cloud"}
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
    # news-v23 phase 3: the theme post's own verdict-word scan (THEME_BANNED_RE, every public string) is a SECOND layer
    # over some of these words — off too, so each refusal is shown to be the title rule's
    monkeypatch.setattr(T, "THEME_BANNED_RE", re.compile(r"(?!x)x"))
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
    assert named["image_spec"]["title"].startswith("At least 3 CEOs")      # owner 2026-11-20: never two rows
    assert named["persons"] == ["Jordan Avery", "Sam Lee"]
    unnamed = compose(week(_avery("CTSH", "Contoso Hotels", 900_000.0, None),
                           _avery("FBRS", "Fabrikam Resorts", 400_000.0, None)), MON)
    assert unnamed["image_spec"]["title"].startswith("At least 3 CEOs") and unnamed["persons"] == []
    assert T._distinct_people((_avery("CTSH", "Contoso Hotels", 900_000.0),
                                   _avery("FBRS", "Fabrikam Resorts", 400_000.0, None),)) == 2
    assert T._distinct_people((_avery("CTSH", "Contoso Hotels", 900_000.0, "Jordan T. Avery"),
                                   _avery("FBRS", "Fabrikam Resorts", 400_000.0),)) == 1


def test_revalidate_refuses_a_fact_sheet_whose_rows_name_one_person_twice():
    """create_posts' path: a stored package whose fact sheet repeats a person never revalidates."""
    good = week(_avery("CTSH", "Contoso Hotels", 900_000.0), _avery("FBRS", "Fabrikam Resorts", 400_000.0, "Sam Lee"))
    out = compose(good, MON)
    assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(good)), run_date=MON) == []
    bad = replace(good, rows=(good.rows[0], replace(good.rows[1], person_name="Jordan Avery"), *good.rows[2:]))
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
        "The CEO of Lowe's disclosed buying $2.3 million of the company's stock in a Form 4 filed Nov 12.",
        "The filing is by the CEO of Lowe's.")] == ["adds_nothing"]
    # honest: a definition, a name the headline lacks, the next fact, a single figure beside a new one
    for head, para in [
        ("Fabrikam reported EPS of $4.86 vs an analyst estimate of $4.50. The EPS basis can differ from the "
         "official accounts.", "An analyst estimate is the average figure analysts published before the report."),
        ("GameStop's CEO disclosed buying $74.4 million of the company's stock in Form 4s filed Nov 10-11.",
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
    # news-v23: an earnings post's ".basis" headlines state the basis paragraph; its company paragraphs (each one's
    # figures) are stated by no headline (the headline names the companies, never their figures)
    basis, estimate = T.LEXICON["er.m.basis"], T.LEXICON["er.estimate"]
    for rec in (earnings(), earnings_profit(), earnings(others=3)):
        p = _parts(rec, EARNINGS_RUN)
        assert p.image_paragraphs[-2:] == [basis, estimate] and p.video_paragraphs == p.image_paragraphs
        for h in p.image_headlines:
            want = {basis} if "basis can differ" in h else set()
            assert p.stated_by_headline.get(h, frozenset()) == want, h
        assert any("basis" not in h for h in p.image_headlines)        # the plain fallbacks exist
    for factory in (public_commitment, tesla_spacex, intel_stake):          # a one-stake post
        p = _parts(factory(), STAKES_RUN)
        source = [x for x in p.image_paragraphs if x.startswith("Source: ")]
        assert len(source) == 1
        for h in p.image_headlines:
            assert p.stated_by_headline.get(h, frozenset()) == (set(source) if "Source: " in h else set()), h
        assert any("Source: " not in h for h in p.image_headlines)
    # news-v23: a post of two or more stakes — its first line states no stake, so it leaves out no paragraph
    for factory in (nvidia_stakes, nvidia_stakes_more, berkshire_mixed):
        p = _parts(factory(), STAKES_RUN)
        assert p.stated_by_headline == {}
    # news-v23: the Congress Count's long first line states every company's count, so the company paragraphs are left
    # out under it (and only under it); "counted once per company" is a paragraph every headline keeps
    p = _parts(congress(), CONGRESS_RUN)
    rows = [x for x in p.image_paragraphs if "members of Congress disclosed purchases in" in x]
    assert len(rows) == 3 and T.LEXICON["cg.m.p.once"] in p.image_paragraphs
    long_head = p.image_headlines[0]
    assert long_head.endswith("2 of Contoso and 2 of Northwind.")
    for h in p.image_headlines:
        # the long line states every count; the lead-only lines (the tightest budgets) the lead's
        want = set(rows) if h == long_head else ({rows[0]} if h.startswith("3 members") else set())
        assert p.stated_by_headline.get(h, frozenset()) == want, h
    assert sum(1 for h in p.image_headlines if not p.stated_by_headline.get(h)) == 2
    # Form 4: the role-only line under the long and short headlines; never the named line
    for rec, line in [(week(LOW), "The filing is by the CEO of Lowe's."),
                      (week(buy("HSY", "Hershey", "director", None, 150_000.0, 812.0, 1, [date(2026, 11, 14)]),
                            series="insider_buys"), "The filing is by a director of Hershey.")]:
        # owner decisions 2026-11-20: a Form 4 post is always a roundup — an unnamed lead row's role-only line is no
        # paragraph at all (its video headlines say it); the map still names it under the two that state it
        p = _parts(rec, MON)
        long_h, short_h, min_h = p.video_headlines
        assert line not in p.video_paragraphs and line not in p.image_paragraphs
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
        bodies, _dropped, _texted = T._caption_bodies(parts, spec, run_date, "live", False)
        for f, body in bodies.items():
            head, *paras = body.split("\n\n")
            assert [r for p in paras for r in _restatements(head, p)] == [], (budget, f)


# ── news-v12: the post-commit copy check (2026-10-10) ─────────────────────────

_WORKER_FONT = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")


def _logo_entries(out):
    """`output.logos` as script_service attaches it, every logo a wordmark (the widest case)."""
    return [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
             "height": None} for r in out["logo_refs"]]


#: news-v23 (company stakes): every stake sample, by factory.
_STAKE_POSTS = [nvidia_stakes, nvidia_stakes_more, berkshire_mixed, lambda: one(tesla_spacex()),
                lambda: one(intel_stake()), lambda: one(public_commitment())]
_STAKE_POST_IDS = ["nvidia3", "nvidia4", "berkshire_mixed", "spacex", "intel", "commitment"]


@pytest.mark.parametrize("factory", _STAKE_POSTS, ids=_STAKE_POST_IDS)
def test_a_stake_cover_is_the_pictures_list_and_never_a_value_today(factory):
    """news-v23 (cover_13f_berkshire.jpg's idea on every template video; owner 2026-11-19): the opening card — the
    video's first frame, the cover TikTok and Instagram pin — is the picture's own list: the title, the investor's
    plate (its logo, its ticker chip) and the first STAKES_COVER_ROWS rows — each with its basis word (`label`) over its
    figure and its as-of line, so no figure ever stands without its basis and date (the news-v12 finding) — and "+N
    more stakes in the caption" when the post holds more. Every string the cover draws is one the server allows."""
    rec = factory()
    out = compose(rec, STAKES_RUN)
    card, spec = out["opening_card"], out["image_spec"]
    rows = spec["sections"][0]["rows"]
    assert card["headline"] == spec["title"] and card["kicker"] == spec["kicker"] == "COMPANY STAKES"
    assert card["logos"] == [rec.investor.symbol] and card["chip"] == spec["header"]["chip"] == rec.investor.symbol
    assert spec["header"] == {"logo": rec.investor.symbol, "name": rec.investor.name, "chip": rec.investor.symbol}
    assert card["list"]["rows"] == rows[:T.STAKES_COVER_ROWS] and "figure" not in card
    n, k = len(rec.stakes), T.STAKES_COVER_ROWS
    assert len(rows) == n <= T.STAKES_PICTURE_ROWS == R.STAKES_POST_STAKES[1] and k == 3
    more = {1: "+1 more stake in the caption"}.get(n - k, f"+{n - k} more stakes in the caption") if n > k else None
    assert "more" not in spec["sections"][0] and card["list"].get("more") == more
    for r, row in zip(rec.stakes, rows):
        assert row["label"] == T.LEXICON[f"st.label.{r.value_basis}"] and row["cells"][2] == T.money_image(r.value_usd)
        assert row["cells"][1].startswith(f"As of {T.date_image(r.as_of)} · ")
        assert row["logo"] == row["chip"] == r.investee.symbol and row["cells"][0] == r.investee_name
    assert onscreen.validate_opening_card(card, {r["key"] for r in out["logo_refs"]}) is None
    allowed = set(onscreen.opening_strings(card, _logo_entries(out)))
    assert {x for row in card["list"]["rows"] for x in (row["chip"], row["label"], *row["cells"])} <= allowed
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("factory", _STAKE_POSTS, ids=_STAKE_POST_IDS)
def test_the_worker_draws_a_stake_list_cover_exactly_as_the_server_allows(factory):
    """The worker's `opening_spec` turns the card's `list` into the list cover (`news_layouts.cover_image`), and what it
    draws is inside `opening_strings` plus the run's short footer and the brand row's word — laid out with a step of
    room to spare on every engine."""
    pytest.importorskip("PIL")
    from marketing import cards
    out = compose(factory(), STAKES_RUN)
    logos = _logo_entries(out)
    table = cards.logo_table({"logos": logos}, {})
    spec = cards.opening_spec(out["opening_card"], table, footer=out["image_footer"], series="company_stakes")
    assert spec.image is not None
    allowed = set(onscreen.opening_strings(out["opening_card"], logos)) | {out["image_footer"], cards.BRAND_ROW_WORD}
    assert set(cards.onscreen_strings(spec)) <= allowed
    for engine in ["basic"] + (["raqm"] if cards.raqm_available() else []):
        layout = cards.layout_card(spec, font_path=_WORKER_FONT, layout_engine=engine)
        assert layout.step < cards.RAMP_STEPS


@pytest.mark.parametrize("source, as_of, shown", [
    ("Synopsys 8-K (Dec 1, 2025)", date(2025, 12, 1), "Synopsys 8-K"),
    ("Corning 10-Q (quarter ended Jun 30, 2026)", date(2026, 6, 30), "Corning 10-Q"),
    ("Lumentum 10-K (fiscal year ended Jun 27, 2026)", date(2026, 6, 27), "Lumentum 10-K"),
    # a document dated after its figure keeps its date (it is not the row's as-of date)
    ("Intel 8-K (Dec 29, 2025)", date(2025, 12, 26), "Intel 8-K (Dec 29, 2025)"),
    ("Corning 10-Q (quarter ended Jun 30, 2026)", date(2026, 6, 29), "Corning 10-Q (quarter ended Jun 30, 2026)"),
    ("Tesla 10-Q (Q2 2026)", date(2026, 6, 30), "Tesla 10-Q (Q2 2026)"),
    ("Microsoft, NVIDIA and Anthropic release (Nov 18, 2025)", date(2025, 11, 18),
     "Microsoft, NVIDIA and Anthropic release"),
    ("Berkshire Hathaway 2025 shareholder letter", date(2025, 12, 31), "Berkshire Hathaway 2025 shareholder letter"),
])
def test_a_stake_rows_source_drops_only_a_date_its_as_of_line_already_prints(source, as_of, shown):
    """stakes_nvidia.jpg: "As of Dec 1, 2025 · Synopsys 8-K" — a source title's trailing "(<date>)" / "(quarter ended
    <date>)" / "(fiscal year ended <date>)" is left off the picture ONLY when that date IS the row's as-of date (it is
    printed beside it: nothing is lost); every caption keeps the title whole. Mutation-checked by hand (2026-11-20):
    the date comparison removed from `_stake_source_shown`, the Intel and Jun 29 rows turned red."""
    assert T._stake_source_shown(source, as_of) == shown
    out = compose(stake(source_title=source, as_of=as_of), STAKES_RUN)
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][1] == f"As of {T.date_image(as_of)} · {shown}"
    assert source in out["captions"]["facebook"]


def test_a_four_stake_post_lists_the_fourth_in_every_caption_that_rides_the_cover():
    """Review 2026-11-20: a post holds at most four stakes (RESTYLE_SPEC §2.5) and the picture draws every one — so no
    caption owes the PICTURE a "+N more" (X and Bluesky need nothing: at 6+ stakes they used to be dropped). The cover
    draws three (`STAKES_COVER_ROWS`) and says "+1 more stake in the caption": every caption that can ride the VIDEO
    keeps the fourth stake — the long ones its whole paragraph (with its source), the short Threads its short form —
    else that outlet is dropped, never posted beside a promise it does not keep. Mutation-checked by hand (2026-11-20):
    `field_required` for x / bluesky set to 0, the X assert turned red at a tight budget (five stakes, before the cap)."""
    rec = nvidia_stakes_more()
    out = compose(rec, STAKES_RUN)
    fourth = rec.stakes[-1]
    assert fourth.investee.symbol == "GLW" and len(rec.stakes) == R.STAKES_POST_STAKES[1] == 4
    assert out["opening_card"]["list"]["more"] == "+1 more stake in the caption"
    assert "more" not in out["image_spec"]["sections"][0]
    long_p = "NVIDIA invested $500 million in Corning, as of Jun 30. Source: Corning 10-Q (quarter ended Jun 30, 2026)."
    short_p = "Corning: $500 million invested, as of Jun 30."
    for f in ("facebook", "linkedin", "instagram", "youtube_description", "tiktok"):
        assert long_p in out["captions"][f], f
    assert short_p in out["captions"]["threads"]
    assert out["dropped_outlets"] == {} and out["text_outlets"] == []
    # the extra video posts' captions (Batch 3) keep the cover's promise too, in either form
    sheet_ = sheet(rec)
    for platform in ("facebook", "linkedin", "threads"):
        cap = T.post_copy.extra_video_caption(platform, out["posts"][platform]["caption"], STAKES_RUN, "template")
        assert T.extra_video_problems(out, fact_sheet=sheet_, run_date=STAKES_RUN, platform=platform,
                                      caption=cap) == [], platform
    # the picture promises nothing: X and Bluesky need no row (they list from the top when they fit)
    parts = _parts(rec, STAKES_RUN)
    assert parts.field_required["x"] == parts.field_required["bluesky"] == 0
    assert parts.field_required["threads"] == parts.field_required["tiktok"] == 1


def test_a_stake_post_ranks_its_rows_by_figure_and_draws_every_one():
    """stakes_nvidia.jpg: the largest figure first, an equal figure the EARLIER as-of date first (Synopsys' Dec 1, 2025
    before Coherent's Mar 2, 2026, as the approved mock), then the stake id — and every stake of the post drawn (review
    2026-11-20: at most four, `R.STAKES_POST_STAKES`); a fifth stake is refused by the record itself."""
    rec = nvidia_stakes_more()
    assert [r.investee.symbol for r in rec.stakes] == ["INTC", "SNPS", "COHR", "GLW"]
    out = compose(rec, STAKES_RUN)
    assert [r["chip"] for r in out["image_spec"]["sections"][0]["rows"]] == ["INTC", "SNPS", "COHR", "GLW"]
    assert out["source_ref"] == "news:company_stakes:" + "+".join(r.stake_id for r in rec.stakes)
    five = (intel_stake(), synopsys_stake(), coherent_stake(), lumentum_stake(), corning_stake())
    with pytest.raises(ValueError, match="stakes"):
        R.CompanyStakes(series="company_stakes", investor=five[0].investor, stakes=tuple(sorted(five, key=R.stake_rank_key)))
    assert T.STAKES_PICTURE_ROWS == R.STAKES_POST_STAKES[1]
    with pytest.raises(T.NewsTemplateRefused) as e:                  # a forged five-stake record: refused whole
        compose(stakes(*five), STAKES_RUN)
    assert e.value.code == "record_invalid"


def apple(**kw):
    """An Apple-shaped fiscal 2025 as FMP's product breakdown (`revenue-product-segmentation`) gives it:
    product and service LINES — Apple's reportable segments are geographic."""
    base = dict(series="money_map", company=co("AAPL", "Apple"), fiscal_year="2025", period_end=date(2025, 9, 27),
                segments=(R.Segment("iPhone", 209_586_000_000.0), R.Segment("Services", 109_158_000_000.0),
                          R.Segment("Wearables, Home and Accessories", 35_686_000_000.0),
                          R.Segment("Mac", 33_708_000_000.0), R.Segment("iPad", 28_023_000_000.0)),
                other_usd=None, eliminations_usd=None, revenue_usd=416_161_000_000.0,
                gross_profit_usd=195_201_000_000.0, operating_profit_usd=133_050_000_000.0,
                net_income_usd=112_010_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


@pytest.mark.parametrize("factory", [apple, costco, rivian, comcast])
def test_money_map_never_calls_a_line_of_its_breakdown_a_segment(factory):
    """Post-commit finding 2: the breakdown is FMP's PRODUCT segmentation — Apple's iPhone, Microsoft's
    LinkedIn — and the copy called each part a "segment" ("Its iPhone segment made up 50% of revenue")
    beside "Figures are as reported": false for the named company. No public string says "segment" now
    but the eliminations bar's own line-item name. Mutation-checked by hand (2026-10-10, in memory): the
    news-v11 `mm.*` wording turns this red."""
    rec = factory()
    out = compose(rec, THU)
    for f, text, _body in T._public_strings(out):
        assert not re.search(r"(?i)segment", text.replace(T.LEXICON["mm.bar.elim"], "")), (f, text)
    assert out["cards"][1]["title"] == "Revenue mix"
    assert T.validate_package(out, record=rec) == []
    if factory is apple:
        # news-v19 (Batch 1): no "Its … business" (a part may be a market or a country), and the neutral
        # "How fiscal … revenue broke down" — Apple's lines are no regions
        assert out["video_script"][1] == "iPhone made up 50% of revenue, and Services made up 26% of revenue."
        assert out["captions"]["linkedin"].split("\n\n")[1] == (
            "How fiscal 2025 revenue broke down: iPhone, $209.6 billion; Services, $109.2 billion; "
            "Wearables, Home and Accessories, $35.7 billion; Mac, $33.7 billion; iPad, $28 billion.")


def test_a_late_form4_row_states_its_trade_date_never_the_two_business_day_rule():
    """Post-commit finding 3: the record admits a trade up to INSIDER_MAX_FILING_LAG_DAYS (30) before its
    filing, and no line stated the trade date — "filed Nov 12" beside "must file a Form 4 within two
    business days of a trade" dated a trade of Oct 17 to about Nov 10. A late row (its last filing more
    than INSIDER_LATE_BUSINESS_DAYS business days after its earliest trade) states its trade date in the narration, on its
    card and in every caption that carried the rule. Mutation-checked by hand (2026-10-10, in memory):
    with `_late_trades` always False this turns red."""
    rec = week(late(traded=(date(2026, 10, 17),), k=1), series="insider_buys")
    out = compose(rec, MON)
    assert "two business days" not in json.dumps(out)
    assert out["video_script"][0] == "The filing is by a director of the company and was filed on November 12th."
    assert out["video_script"][2] == "The filing gives October 17th as the trade date."
    assert out["cards"][2] == {"title": "Trade date", "body": "Oct 17, 2026 · filed Nov 12, 2026"}
    # the lead row's own dates in the VIDEO captions; the picture's captions state the whole roundup's trade window
    # (owner decisions 2026-11-20: a Form 4 post is always a roundup — its two smaller rows traded Nov 15)
    for f in ("tiktok", "instagram", "youtube_description"):
        assert "The filing gives Oct 17 as the trade date. A Form 4 is the report" in out["captions"][f], f
    for f in ("facebook", "linkedin"):
        assert "The filings give trade dates from Oct 17 to Nov 15. A Form 4 is the report" in out["captions"][f], f
    assert T.validate_package(out, record=rec) == []


#: (earliest trade, last filing, the record's window start, run date, late?) — review round 14: the bound
#: counts FEDERAL BUSINESS DAYS after the trade up to the last filing (late when > 2), never calendar days.
#: The weeks of Nov 16 (no holiday) and Nov 9 (Veterans Day, Wed Nov 11) and the week of Columbus Day
#: (Mon Oct 12). The comment on each case is its business-day count, then its calendar-day gap.
_LATE_BUSINESS_DAY_CASES = [
    (date(2026, 11, 16), date(2026, 11, 16), date(2026, 11, 16), date(2026, 11, 23), False),   # 0
    (date(2026, 11, 16), date(2026, 11, 18), date(2026, 11, 16), date(2026, 11, 23), False),   # Mon→Wed: 2, 2
    (date(2026, 11, 16), date(2026, 11, 19), date(2026, 11, 16), date(2026, 11, 23), True),    # Mon→Thu: 3, 3
    (date(2026, 11, 16), date(2026, 11, 20), date(2026, 11, 16), date(2026, 11, 23), True),    # Mon→Fri: 4, 4
    (date(2026, 11, 12), date(2026, 11, 16), date(2026, 11, 16), date(2026, 11, 23), False),   # Thu→Mon: 2, 4
    (date(2026, 11, 13), date(2026, 11, 17), date(2026, 11, 16), date(2026, 11, 23), False),   # Fri→Tue: 2, 4
    (date(2026, 11, 12), date(2026, 11, 17), date(2026, 11, 16), date(2026, 11, 23), True),    # Thu→Tue: 3, 5
    (date(2026, 11, 9), date(2026, 11, 13), date(2026, 11, 9), MON, True),                     # Mon→Fri, holiday: 3, 4
    (date(2026, 11, 9), date(2026, 11, 12), date(2026, 11, 9), MON, False),                    # Mon→Thu, holiday: 2, 3
    (date(2026, 10, 9), date(2026, 10, 14), date(2026, 10, 12), date(2026, 10, 19), False),    # Fri→Wed, holiday Mon: 2, 5
    (date(2026, 11, 12) - timedelta(days=R.INSIDER_MAX_FILING_LAG_DAYS), date(2026, 11, 12), date(2026, 11, 9), MON,
     True),                                                                                     # the record's outer bound
]


@pytest.mark.parametrize("traded, filed, start, run_date, is_late", _LATE_BUSINESS_DAY_CASES,
                         ids=["same_day", "mon_wed", "mon_thu", "mon_fri", "thu_mon", "fri_tue", "thu_tue",
                              "mon_fri_veterans_day", "mon_thu_veterans_day", "fri_wed_columbus_day", "max_lag"])
def test_the_late_bound_is_two_federal_business_days_after_the_trade(traded, filed, start, run_date, is_late):
    """Review round 14 (low): the bound was 4 CALENDAR days ("two business days plus a weekend"), which
    holds only for a Thursday or Friday trade — a Monday trade filed on Friday is four business days on
    (two past the deadline), yet the copy said "within two business days of a trade" beside "filed Nov 13",
    dating a Nov 9 trade to Nov 11. Now: the weekdays after the earliest trade up to the last filing,
    federal holidays excluded (the SEC's calendar), late when more than 2. Mutation-checked by hand
    (2026-10-10, in memory): the calendar-day `_late_trades` turns every case whose two counts disagree
    red (mon_thu, mon_fri, mon_fri_veterans_day, fri_wed_columbus_day)."""
    row = late(traded=(traded,), filings=(filed,), k=1)
    rec = week(row, series="insider_buys", start=start, end=start + timedelta(days=6))
    assert T.INSIDER_LATE_BUSINESS_DAYS == 2
    assert T._late_trades(row) is is_late
    out = compose(rec, run_date)
    assert (out["video_script"][2] == T.LEXICON["ins.l3.explainer"]) == (not is_late)
    assert ("two business days" in json.dumps(out)) == (not is_late)
    if is_late:
        assert out["video_script"][2] == f"The filing gives {T.date_spoken(traded, run_date)} as the trade date."
        assert out["cards"][2]["title"] == "Trade date"
    assert T.validate_package(out, record=rec) == []


def test_the_reviewers_monday_trade_filed_friday_states_its_trade_date():
    """Review round 14's probe, end to end: traded Mon Nov 9, filed Fri Nov 13 (four calendar days, three
    business days with Veterans Day out) — the old bound called it timely and drew "Filed within two
    business days of an insider's trade" beside "filed Nov 13"."""
    rec = week(late(traded=(date(2026, 11, 9),), filings=(date(2026, 11, 13),), k=1), series="insider_buys")
    out = compose(rec, MON)
    assert out["video_script"][0] == "The filing is by a director of the company and was filed on November 13th."
    assert out["video_script"][2] == "The filing gives November 9th as the trade date."
    assert out["cards"][2] == {"title": "Trade date", "body": "Nov 9, 2026 · filed Nov 13, 2026"}
    assert "two business days" not in json.dumps(out)
    for f in ("tiktok", "instagram", "youtube_description"):
        assert "The filing gives Nov 9 as the trade date. A Form 4 is the report" in out["captions"][f], f
    for f in ("facebook", "linkedin"):                       # the roundup's whole trade window (its rows to Nov 15)
        assert "The filings give trade dates from Nov 9 to Nov 15. A Form 4 is the report" in out["captions"][f], f
    assert T.validate_package(out, record=rec) == []


def test_the_business_day_count_is_bounded_and_never_negative():
    """The count stops once it passes the bound (a malformed gap of years costs a few iterations), and a
    trade after the filing counts 0 (timely: the record gate, not the copy, refuses such a row)."""
    assert T._business_days_after(date(2026, 11, 13), date(2026, 11, 9), stop_above=2) == 0
    assert T._business_days_after(date(2020, 1, 1), date(2026, 11, 9), stop_above=2) == 3
    assert T._business_days_after(date(2026, 12, 24), date(2026, 12, 28), stop_above=2) == 1   # Christmas, weekend
    assert T._business_days_after(date(2026, 12, 30), date(2027, 1, 4), stop_above=9) == 2    # New Year's Day


def test_the_late_twin_replaces_the_explainer_wherever_it_lands():
    """L4 after a flag line; two filings ("The filings give …"); a window of dates. And the bound reads the
    LAST filing: a row timely against its first filing but whose trades start more than the bound before
    its last one states its dates (each trade then sits within the bound of whichever filing reported it)."""
    row = late(traded=(date(2026, 10, 20), date(2026, 11, 3)), filings=(date(2026, 11, 10), date(2026, 11, 12)),
               holding="indirect")
    out = compose(week(row, series="insider_buys"), MON)
    assert out["video_script"][2] == T.LEXICON["ins.l3.indirect.all"].format(role_sp="director")
    # owner decisions 2026-11-20: line 4 is always the other rows ("At least 2 more …") — the lead's dates are in its
    # video captions, the roundup's window in the picture's, and the rule nowhere
    assert out["video_script"][3].startswith("At least 2 more ")
    assert "The filings give trade dates from Oct 20 to Nov 3. A Form 4" in out["captions"]["instagram"]
    assert "The filings give trade dates from Oct 20 to Nov 15. A Form 4" in out["captions"]["linkedin"]
    assert "two business days" not in json.dumps(out)
    edge = late(traded=(date(2026, 11, 9), date(2026, 11, 13)), filings=(date(2026, 11, 10), date(2026, 11, 14)))
    out = compose(week(edge, series="insider_buys"), MON)
    assert out["video_script"][2] == "The filings give trade dates from November 9th to November 13th."
    assert "two business days" not in json.dumps(out)


def test_a_roundup_with_a_late_row_says_no_two_business_day_rule_in_any_caption():
    """A roundup's paragraphs speak for every row: with one late row the image paragraphs give the week's
    trade window and the video's (about the lead row and "At least {m} more …") drop the rule.

    Review round 13: so do the narration and the cards. The lead row (GME) is timely, but the line right
    after the explainer — "At least 2 more chief executives … in the same week" — covers the late row,
    and the rule beside it dated HSY's Oct 20 trade to its filing week, on every burned frame, while the
    same video's caption had dropped it. L3 and card 3 now say what a Form 4 is without the rule, the
    choice the video paragraph makes."""
    hsy = late("HSY", "Hershey", "ceo", traded=(date(2026, 10, 20),), filings=(date(2026, 11, 13),), k=1)
    rec = week(GME, LOW, hsy)
    out = compose(rec, MON)
    assert out["video_script"][2] == T.LEXICON["ins.l3.form4"] == (
        "A Form 4 is the report officers and directors file on their own trades.")
    assert out["cards"][2] == {"title": "What a Form 4 is",
                               "body": "The report officers and directors file on their own trades"}
    assert out["video_script"][3].startswith("At least 2 more chief executives")
    assert "two business days" not in json.dumps(out)                       # narration, cards and captions
    for f, body in out["captions"].items():
        assert "two business days" not in body, f
    for f in ("facebook", "linkedin"):
        assert ("The filings give trade dates from Oct 20 to Nov 12. A Form 4 is the report a company's officers "
                "and directors file on their own trades.") in out["captions"][f], f
    assert T.LEXICON["ins.p.form4.late"] in out["captions"]["youtube_description"]
    assert "The filings give" not in out["captions"]["youtube_description"]
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("holdings, para", [
    (("indirect", "indirect", "indirect"),
     "The shares are reported as held indirectly rather than in the buyers' own names."),
    (("direct", "indirect", "direct"), "Some of the shares are reported as held indirectly."),
])
def test_a_roundups_indirect_paragraph_speaks_of_several_buyers(holdings, para):
    """Post-commit finding 4: a 3-CEO roundup's paragraph said "rather than in the buyer's own name" for
    three buyers, and its image note said the PURCHASES are held. Mutation-checked by hand (2026-10-10,
    in memory): with the singular paragraphs on a roundup this turns red."""
    rows = [replace(r, holding=h) for r, h in zip((GME, LOW, SBUX), holdings)]
    out = compose(week(*rows), MON)
    for f in ("facebook", "linkedin"):
        assert para in out["captions"][f] and "buyer's own name" not in out["captions"][f], f
    assert all("shares" in n for n in out["image_spec"]["notes"] if "indirectly" in n)
    # the lead row's own (video) paragraph keeps the singular; the roundup's picture captions speak of several buyers
    single = compose(week(LOW), MON)
    assert T.LEXICON["ins.p.flag.indirect.all"] in single["captions"]["instagram"]
    assert T.LEXICON["ins.p.flag.indirect.some.many"] in single["captions"]["linkedin"]


def test_a_theme_fact_narrated_alone_says_its_own_fiscal_year():
    """Post-commit finding 5: a member's fact may be THEME_FACT_MAX_YEARS old (the breakdown's newest year
    paired with an income statement often lags), and a fact narrated alone said "in its latest reported
    year". It says the record's fiscal year now. Mutation-checked by hand (2026-10-10, in memory): the
    news-v11 `th.one.*` turns this red."""
    rows = _members(6, name=lambda i: f"Northwind Data Systems {i:02d}", seg=lambda i: "Cloud and Data Infrastructure")
    # news-v16: a year older than the newest fact (a December year end's latest beside a later one's)
    rows = [(s, n, g, sh, str(THEME_RUN.year - 1) if i == 0 else fy) for i, (s, n, g, sh, fy) in enumerate(rows)]
    out = compose(theme(rows), THEME_RUN)
    assert out["video_script"][0] == ("Northwind Data Systems 00's largest revenue line in fiscal 2025 was Cloud "
                                      "and Data Infrastructure.")
    assert out["video_script"][1] == ("Northwind Data Systems 01's largest revenue line in fiscal 2026 was Cloud "
                                      "and Data Infrastructure.")
    assert "latest" not in json.dumps(out)
    for entry_id in ("th.one.poss", "th.one.of"):
        assert "in fiscal {fy}" in T.LEXICON[entry_id] and "latest" not in T.LEXICON[entry_id]


def test_congress_cards_keep_the_narrations_hedges():
    """Post-commit finding 6: a card stays on screen for its whole line, and cards 3 and 4 dropped the
    narration's hedges — "Spouse and dependent-child trades" (the reports CAN include them, beside the
    members' own) and "Up to 45 days after the trade" (the deadline, not a bound: late reports exist).
    Mutation-checked by hand (2026-10-10, in memory): the news-v11 bodies turn this red."""
    out = compose(congress(), CONGRESS_RUN)
    # news-v23: line 1 is the other companies' counts, so the spouse hedge rides line 3 with "once per company"
    assert out["cards"][2] == {"title": "Counted once per company",
                               "body": "Can include spouse and dependent-child trades"}
    assert out["cards"][3] == {"title": "Reporting delay", "body": "Due within 45 days of the trade; some are filed later"}
    assert "can include" in out["video_script"][2]
    assert "Up to 45 days" not in json.dumps(out)
    assert T.validate_package(out, record=congress()) == []


# ── news-v13: review round 13 (2026-10-10) ────────────────────────────────────


def _theme_shapes():
    """Every theme shape the builder writes: the two AI chips samples (pairs, a stale fact, a long segment
    name, a member without a fact), the default record (no size), three facts (the explainer line and
    card), four-word names (one fact a line), a name word too wide for a card title (the generic titles),
    and every member with its fact under a whole and an "of its" size (the ".each" headlines)."""
    wide = _members(6, name=lambda i: f"Northwindcorporation {i:02d}", seg=lambda i: "Cloud") + [
        (f"S{a}", f"Fabrikam {a}", "Cloud", 0.5, "2026") for a in "ABC"]
    long = _members(6, name=lambda i: f"Northwind Data Systems {i:02d}", seg=lambda i: "Cloud and Data Infrastructure")
    three = _members(6, seg=lambda i: "Cloud" if i < 3 else None, share=lambda i: 0.5 if i < 3 else None)
    return [ai_chips(), ai_chips_of_more(), theme(), theme(wide), theme(long), theme(three),
            theme(_members(8), theme_size=8), theme(_members(8), theme_size=10)]


def test_a_theme_never_calls_a_revenue_line_a_segment():
    """Review round 13 (medium): a theme's facts come from the same product breakdown as Money Map
    (FMP's revenue-product-segmentation via `_segments`), which news-v12 stopped calling "segments" —
    Apple's iPhone is not one (its reportable segments are geographic). The theme still narrated
    "Apple's largest revenue segment was iPhone" and captioned "Largest revenue segments, as each company
    reports them", a claim false for that company. No public theme string says "segment" now: not the
    narration, a card, the cover, the image, the alt text, a caption or a variant offered but not picked.
    Mutation-checked by hand (2026-10-10, in memory): the news-v12 `th.*` entries turn this red.

    Review round 14 (medium): nor the record's "Source:" label (`company_news_rules.SOURCE_LABELS`),
    burned into the image footer and closing the alt text — "company segment reporting" made the same
    false claim on every theme post, and this test exempted it. No exemption now: the footer, the alt
    text's source line and the whole package are scanned as composed. Mutation-checked by hand
    (2026-10-10, in memory): the old label turns this red."""
    for rec in _theme_shapes():
        out = compose(rec, THEME_RUN)
        blob = json.dumps(out, ensure_ascii=False)
        assert not re.search(r"(?i)segment", blob), (rec.theme_size, re.findall(r".{60}segment.{20}", blob))
        for text in _offered(rec) + [R.source_label(rec), out["image_spec"]["footer"], out["image_footer"],
                                     *out["image_post"]["paragraphs"]]:
            assert not re.search(r"(?i)segment", text), text
        assert "reports them" not in blob
        # news-v23 (restyle 2026-11-19): the picture's footer is the family's short line; the alt text keeps the
        # theme's own source label (below)
        assert out["image_spec"]["footer"] == out["image_footer"] == "Not investment advice · Source: company filings"
        assert out["image_post"]["paragraphs"][-1].startswith(
            "Source: company revenue breakdowns; grouping by Caydex. "), out["image_post"]["paragraphs"]
        assert T.validate_package(out, record=rec) == []
    for entry_id, text in T.LEXICON.items():
        if entry_id.startswith("th."):
            assert "segment" not in text.casefold() and "reports them" not in text, entry_id
    out = compose(ai_chips(), THEME_RUN)
    assert out["video_script"][0] == "Nvidia's largest revenue line in fiscal 2026 was Data Center."
    # news-v23 phase 3: one caption paragraph per company, its figure and its own fiscal year; the cover's title is the
    # theme's (its list says the rest)
    assert out["captions"]["linkedin"].split("\n\n")[1] == "NVIDIA: Data Center, 88% of fiscal 2026 revenue."
    assert out["opening_card"]["headline"] == "AI chips"
    three = compose(_theme_shapes()[5], THEME_RUN)
    # news-v19 (the review of 2026-10-16, TH-1): a revenue line can be a region too (a member's breakdown can be
    # regional), so the explainer says so
    assert three["video_script"][2] == "A revenue line is one product, business or region in a company's revenue breakdown."
    assert three["cards"][2] == {"title": "What a revenue line is",
                                 "body": "One product, business or region in a revenue breakdown"}
    assert compose(_theme_shapes()[3], THEME_RUN)["cards"][0]["title"] == "Largest revenue lines"


#: (lead row, the rest, L3, card 3) — the roundup's explainer, by which of its rows are late.
_LATE_HSY = late("HSY", "Hershey", "ceo", traded=(date(2026, 10, 20),), filings=(date(2026, 11, 13),), k=1)
#: (lead row, the rest, L3, card 3, the YouTube description's Form 4 paragraph) — a roundup's explainer by
#: which of its rows are late (rows are ordered by amount, so a late LEAD is the largest purchase).
_ROUNDUP_EXPLAINERS = [
    (GME, (LOW, SBUX), "Officers and directors must file a Form 4 within two business days of a trade.",
     {"title": "What a Form 4 is", "body": "Filed within two business days of an insider's trade"},
     T.LEXICON["ins.p.form4"]),
    (GME, (SBUX, _LATE_HSY), "A Form 4 is the report officers and directors file on their own trades.",
     {"title": "What a Form 4 is", "body": "The report officers and directors file on their own trades"},
     T.LEXICON["ins.p.form4.late"]),
    (replace(_LATE_HSY, amount_usd=80_000_000.0), (GME, SBUX), "The filing gives October 20th as the trade date.",
     {"title": "Trade date", "body": "Oct 20, 2026 · filed Nov 13, 2026"},
     "The filing gives Oct 20 as the trade date. " + T.LEXICON["ins.p.form4.late"]),
]


@pytest.mark.parametrize("lead, rest, l3, c3, yt_form4", _ROUNDUP_EXPLAINERS,
                         ids=["all_timely", "another_late", "lead_late"])
def test_a_roundups_explainer_matches_its_video_paragraph(lead, rest, l3, c3, yt_form4):
    """Review round 13 (low): the roundup video narrated and drew "within two business days of a trade"
    right before "At least {m} more … in the same week" when the lead row was timely but another row was
    late, though that video's caption had already dropped the rule. Narration L3 and card 3 now make the
    caption's choice (`form4_video`): the rule only when no row is late; the lead's trade dates when the
    lead is late; else the Form 4 without the rule and without dates. Mutation-checked by hand
    (2026-10-10, in memory): without the `late_other` branch the "another_late" case turns red."""
    rec = week(lead, *rest)
    out = compose(rec, MON)
    assert out["video_script"][2] == l3 and out["cards"][2] == c3
    assert out["video_script"][3].startswith("At least 2 more chief executives")
    yt = out["captions"]["youtube_description"]
    assert yt_form4 in yt
    assert ("two business days" in l3) == ("two business days" in yt) == ("two business days" in json.dumps(out))
    if lead is GME and _LATE_HSY in rest:
        assert "The filing gives" not in yt                                   # the lead's filing gives no date
    assert T.validate_package(out, record=rec) == []


def test_an_insider_roundup_with_only_a_later_row_late_draws_no_rule():
    """The same choice on an Insider Buys roundup (a CFO lead, a late director after it)."""
    nke = buy("NKE", "Nike", "cfo", None, 1_250_000.0, 18_500.0, 1, [date(2026, 11, 10)])
    rec = week(nke, late(traded=(date(2026, 10, 20),), filings=(date(2026, 11, 13),), k=1), series="insider_buys")
    out = compose(rec, MON)
    assert out["video_script"][2] == T.LEXICON["ins.l3.form4"]
    assert out["video_script"][3] == ("At least 2 more directors disclosed buying their own company's stock in the "
                                      "same week.")
    assert out["cards"][2]["body"] == T.LEXICON["ins.c3.form4.body"]
    assert "two business days" not in json.dumps(out)
    assert T.validate_package(out, record=rec) == []


# ── news-v14: review round 14 (2026-10-10) ────────────────────────────────────


def _all_offered_text(rec, run_date) -> str:
    """Every string the builder OFFERS (each narration variant, card, headline, title, paragraph, the image
    and opening specs), picked or not, as one JSON blob."""
    from dataclasses import asdict
    return json.dumps(asdict(_parts(rec, run_date)), ensure_ascii=False, default=sorted)


@pytest.mark.parametrize("rec, run_date", [
    (berkshire(filed_on=sec_13f_due_date(2026, 3)), TUE),     # on the due date, 47 days on
    (berkshire(filed_on=date(2026, 11, 20)), date(2026, 11, 23)),       # a late filer inside the season, 51
    (berkshire(amended_on=date(2026, 12, 18)), date(2026, 12, 21)),     # an amendment, 79 days on
    (pershing(), TUE),                                                  # a listed holding takes L4 and card 4
], ids=["on_due_date", "late_filer", "amended", "listed_l4"])
def test_a_13f_states_its_deadline_as_a_deadline_never_a_bound(rec, run_date):
    """Review round 14 (medium): the 13F explainer said a 13F "is filed up to 45 days later", but the due
    date rolls past weekends and federal holidays (`sec_13f_due_date`: 2026-Q3's is Nov 16, 47 days after
    Sep 30), the season admits late filers and an amendment can come months on — so the same video showed
    "filed Nov 16, 2026" beside a 45-day maximum. It says "is due about 45 days later" now, in the narration
    (both variants), on card 4 and in the caption paragraph. Mutation-checked by hand (2026-10-10, in
    memory): any one of the four news-v13 entries turns this red."""
    assert sec_13f_due_date(2026, 3) == date(2026, 11, 16)
    out = compose(rec, run_date)
    blob = json.dumps(out, ensure_ascii=False)
    for text in (blob, _all_offered_text(rec, run_date)):
        assert "up to 45 days" not in text and "filed up to" not in text
    assert "due about 45 days" in blob
    for f in ("facebook", "linkedin", "instagram", "youtube_description"):
        assert T.LEXICON["f13.p.explainer"] in out["captions"][f], f
    assert T.LEXICON["f13.p.explainer"].startswith(
        "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; ")
    if rec.filer_symbol == "BRK-B":
        # news-v23 (the 13F list): line 4 is the explainer, its short variant where the kind lines fill the narration
        assert out["video_script"][3] in ("A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days "
                                          "later.", "A 13F is due about 45 days after a quarter's end.")
        assert out["cards"][3] == {"title": "About 13F filings", "body": "U.S. stock holdings, due about 45 days after the quarter"}
    assert T.validate_package(out, record=rec) == []


def test_no_13f_entry_states_the_deadline_as_a_bound():
    """The lexicon itself, every 13F entry (the short narration variant included)."""
    assert T.LEXICON["f13.l4.explainer.short"] == "A 13F is due about 45 days after a quarter's end."
    for entry_id, text in T.LEXICON.items():
        if entry_id.startswith("f13."):
            assert "up to" not in text and "at most" not in text and "within 45" not in text, entry_id


# ── news-v15: review round 15 (2026-10-10) ────────────────────────────────────

CG_DEADLINE_LINE = "Reports are due within 45 days of a trade; some come later, so the trades may be older."


def test_the_congress_narration_states_45_days_as_a_deadline_never_a_bound():
    """Review round 15 (medium): `cg.l4` said "A trade can be disclosed up to 45 days after it happens", but the
    adapter counts every in-month 'purchase' row by its DISCLOSURE date and never reads the trade date, so a
    report filed months late (the STOCK Act's late fee exists for them) is counted — beside a narrated 45-day
    bound. It states the deadline now, as card 4 already did ("Due within 45 days of the trade; some are filed
    later"), in the narration and in every caption that carries the paragraph. Mutation-checked by hand
    (2026-10-10, in memory): the news-v14 `cg.l4` turns this red."""
    rec = congress()
    out = compose(rec, CONGRESS_RUN)
    assert T.LEXICON["cg.l4"] == CG_DEADLINE_LINE
    assert out["video_script"][3] == CG_DEADLINE_LINE
    assert out["cards"][3] == {"title": "Reporting delay", "body": "Due within 45 days of the trade; some are filed later"}
    for f in ("linkedin", "instagram", "facebook", "youtube_description"):
        assert CG_DEADLINE_LINE in out["captions"][f], f
    for text in (json.dumps(out, ensure_ascii=False), _all_offered_text(rec, CONGRESS_RUN)):
        assert "up to 45 days" not in text and "disclosed up to" not in text
    for entry_id, text in T.LEXICON.items():
        if entry_id.startswith("cg."):
            assert "up to" not in text and "at most" not in text, entry_id
    assert T.validate_package(out, record=rec) == []


def thirteen_f_partial():
    """A 13F record carrying FEWER moves than its counts — the usual shape (THIRTEEN_F_MAX_MOVES and the
    adapter's materiality floors): one move per kind, counts new 2, more 3, fewer 4, gone 1."""
    return berkshire(
        moves=(move("ZQCT", "Contoso", "increased", shares=3_000_000.0, prev_shares=1_000_000.0,
                    value_usd=300_000_000.0),
               move("ZQFB", "Fabrikam", "decreased", shares=1_000_000.0, prev_shares=2_000_000.0,
                    value_usd=100_000_000.0),
               move("ZQNW", "Northwind", "newly_reported", shares=500_000.0, prev_shares=0.0, value_usd=50_000_000.0),
               move("ZQTT", "Tailspin Toys", "no_longer_reported", shares=0.0, prev_shares=800_000.0,
                    prev_value_usd=40_000_000.0)),
        counts=(("newly_reported", 2), ("increased", 3), ("decreased", 4), ("no_longer_reported", 1)))


def test_a_13f_alt_text_says_how_many_changes_the_picture_leaves_to_the_caption():
    """Review round 15 (low), carried to the news-v23 list: the alt text describes exactly what the picture draws — its
    rows, in its order — and, when the record holds more than the picture's six, says how many more the caption lists
    ("2 more changes are listed in the caption.", the picture's own "+2 more changes in the caption"); a list drawn whole
    says nothing more."""
    rec = f13s_list8()
    out = compose(rec, SPOT_SAT)
    head, rows, more, source = out["image_post"]["paragraphs"]
    assert head.startswith("It lists 6 of the filing's holding changes from the quarter before: ")
    drawn = [r["cells"][0] for r in out["image_spec"]["sections"][0]["rows"]]
    assert [item.split(",")[0] for item in rows.rstrip(".").split("; ")] == drawn
    assert more == "2 more changes are listed in the caption." and source.startswith("Source: SEC Form 13F.")
    whole = compose(berkshire(), TUE)
    assert len(whole["image_post"]["paragraphs"]) == 3
    assert not re.search(r"more changes? (?:are|is) listed", json.dumps(whole["image_post"]))


def test_the_roundup_alt_text_lists_each_buyers_total_never_each_purchase():
    """Review round 15 (low): `ins.alt.rows` said "The image lists each purchase by company, role and amount",
    but a row is one person's TOTAL over every purchase line in the window (GameStop's row is 2 purchases, the
    narration says so), so a screen-reader user was told $74.4M was one purchase. Mutation-checked by hand
    (2026-10-10, in memory): the news-v14 entry turns this red."""
    rec = ceo_roundup()
    assert rec.rows[0].purchases == 2
    out = compose(rec, MON)
    assert out["image_post"]["paragraphs"][0].startswith(
        "The image lists each buyer's total by company, role and amount: GameStop, CEO, $74.4M; ")
    for text in (json.dumps(out, ensure_ascii=False), _all_offered_text(rec, MON)):
        assert "each purchase" not in text


#: Wording that COUNTS a row's Form 4 documents as one — never on a row with one filing date and several
#: purchase lines (the record keeps distinct filing days, not documents).
_ONE_FILING_PHRASES = ("a Form 4 filed", "Form 4s", "The filing is", "The filing names", "The filing gives",
                       "It reports", "was filed on", "the filing's")


@pytest.mark.parametrize("person, traded", [
    ("Ryan Cohen", None), (None, None),
    (None, (date(2026, 10, 14), date(2026, 10, 17))),          # a late row: its trade dates, count-neutral too
], ids=["named", "role_only", "late"])
def test_one_filing_date_with_several_purchases_never_counts_its_filings(person, traded):
    """Review round 15 (low, PLAUSIBLE): the adapter builds `filing_dates` from distinct DAYS and InsiderBuy
    carries no accession number, so two Form 4s filed the same day (one per trade date, both filed Wednesday)
    are one date — and the post said "in a Form 4 filed Oct 8", "The filing is by …", "It reports 3
    purchases", "The filing names …". Such a row is worded count-neutrally now (`_filing_count` "neutral");
    a single purchase line is provably one filing and two dates provably several, so those keep their words.
    Mutation-checked by hand (2026-10-10, in memory): `_filing_count` answering "one" for one date turns
    this red."""
    filed = date(2026, 11, 10)
    row = buy("GME", "GameStop", "ceo", person, 74_400_000.0, 3_000_000.0, 3, [filed])
    if traded:
        row = replace(row, earliest_trade_date=traded[0], latest_trade_date=traded[-1])
    assert T._filing_count(row) == "neutral"
    rec = week(row)
    out = compose(rec, MON)
    # owner decisions 2026-11-20: a Form 4 post is always a roundup, whose own headline ("… in Form 4s filed Nov 9-15")
    # rightly counts SEVERAL companies' filings — the lead row's own wording is what must stay count-neutral
    roundup_head = re.compile(r"[^.\n\"]*in Form 4s filed (?:Nov 9-15|the same week)\.( The largest of these: "
                              r"[^.\n]*\.)?")
    for text in (json.dumps(out, ensure_ascii=False), _all_offered_text(rec, MON)):
        text = roundup_head.sub("", text)
        for phrase in _ONE_FILING_PHRASES:
            assert phrase not in text, phrase
    assert out["video_script"][0] == "The filer is the company's chief executive, and the filing date is November 10th."
    assert out["video_script"][1] == "3 purchases are reported, 3 million shares in total, for $74.4 million."
    assert out["captions"]["tiktok"].split("\n")[0] == (
        "GameStop's CEO disclosed buying $74.4 million of the company's stock on Form 4, filed Nov 10.")
    if person:
        assert "The filer is Ryan Cohen, the CEO of GameStop." in out["captions"]["instagram"]
    if traded:
        assert out["video_script"][2] == ("The purchases are reported with trade dates from October 14th to "
                                          "October 17th.")
    assert T.validate_package(out, record=rec) == []


def test_the_filing_count_is_neutral_only_where_the_record_cannot_tell():
    one = buy("LOW", "Lowe's", "ceo", None, 2_300_000.0, 9_812.0, 1, [date(2026, 11, 12)])
    assert T._filing_count(one) == "one"                     # one line → one document
    assert T._filing_count(GME) == "many"                    # two filing dates → two documents at least
    assert T._filing_count(replace(GME, filing_dates=(date(2026, 11, 10),))) == "neutral"
    # the provable cases keep their words
    assert compose(week(one), MON)["captions"]["tiktok"].split("\n")[0].endswith("in a Form 4 filed Nov 12.")
    assert "in Form 4s filed Nov 10-11" in compose(week(GME), MON)["captions"]["tiktok"].split("\n")[0]
    # every neutral narration variant fits LINE_WORDS for every role, so a long script can still step down
    lo, hi = T.LINE_WORDS
    for role in ("ceo", "cfo", "director"):
        for entry_id in ("ins.l1.role.neutral", "ins.l1.role.short.neutral"):
            assert lo <= len(neutral(entry_id).replace(NEUTRAL["role_np_sp"], T.LEXICON[f"role.{role}.np.sp"])
                             .split()) <= hi, (entry_id, role)


# ── news-v16: review round 16 (2026-10-10) ────────────────────────────────────


def _theme_at(rows, run_date, **kw):
    """`rows` as a theme whose member list is ten days old on `run_date` (inside THEME_MAX_AGE_DAYS)."""
    return theme(rows, tickers_as_of=run_date - timedelta(days=10), **kw)


#: The finding's probe, run on THEME_RUN (Nov 16, 2026): Contoso's fact is from fiscal 2024, the others
#: from fiscal 2025 and 2026.
_PROBE_ROWS = [("CTSO", "Contoso", "Gaming", 0.62, "2024"), ("FBKM", "Fabrikam", "Data Center", 0.71, "2026"),
               ("ADVW", "Adventure Works", "Outdoor gear", 0.55, "2025"), ("NWND", "Northwind", "Cloud", 0.48, "2026"),
               ("TLSP", "Tailspin", "Drones", 0.39, "2026"), ("WGBK", "Woodgrove", "Payments", 0.66, "2026")]


def test_a_theme_fact_older_than_the_sub_lines_years_carries_its_own_year():
    """Review round 16 (low, CONFIRMED by probe) found a fiscal 2024 share drawn beside fiscal 2026 ones with no year —
    then the fact was dropped. news-v23 phase 3 (owner 2026-11-19: "for AI CHIPS, i don't see ARM, Intel and SNPS? add
    for me") draws it again, TAGGED with its own year: the tile reads "Gaming, FY2024 · 62% of revenue", the caption
    "62% of fiscal 2024 revenue", the narration its own fiscal year — never implied current; the sub line states only
    the untagged facts' years. Mutation-checked by hand (2026-11-20): with the tag dropped from the tile, this turned
    red (and `validate_package` still passed — the tag is this test's to guard)."""
    rec = _theme_at(_PROBE_ROWS, THEME_RUN, theme_size=6)
    out = compose(rec, THEME_RUN)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["Contoso"] == "Gaming, FY2024 · 62% of revenue" and tiles["Fabrikam"] == "Data Center · 71% of revenue"
    assert "Gaming · 62%" not in json.dumps(out)                                 # never untagged
    assert "Contoso: Gaming, 62% of fiscal 2024 revenue." in out["captions"]["linkedin"]
    assert out["image_spec"]["subtitle"] == ("6 companies · each company's largest business and its share of total "
                                             "revenue · fiscal 2025 or 2026")
    for line in out["video_script"]:
        if "Gaming" in line:
            assert "fiscal 2024" in line, line
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("run_date, years, kept", [
    # before June a December year end may not have filed fiscal run_year - 1: its run_year - 2 fact is its latest
    (date(2027, 3, 15), ("2025", "2026"), {"2025", "2026"}),
    (date(2027, 5, 31), ("2025", "2026"), {"2025", "2026"}),
    # ... unless a fact two fiscal years newer sits beside it (THEME_FACT_SPREAD_YEARS)
    (date(2027, 3, 15), ("2025", "2027"), {"2027"}),
    (date(2027, 3, 15), ("2025", "2026", "2027"), {"2026", "2027"}),
    # from June every company has reported fiscal run_year - 1
    (date(2027, 6, 1), ("2025", "2026"), {"2026"}),
    (THEME_RUN, ("2024", "2025", "2026"), {"2025", "2026"}),
    (THEME_RUN, ("2025", "2026"), {"2025", "2026"}),
], ids=["mar_two_back", "may31_two_back", "mar_spread_two", "mar_three_years", "jun1_two_back", "nov_three_years",
        "nov_adjacent"])
def test_a_theme_fact_is_untagged_only_while_it_can_be_the_latest_and_beside_facts_at_most_a_year_newer(
        run_date, years, kept):
    """news-v16's rule now decides which facts the SUB LINE states (untagged); every other fact within
    `rules.THEME_TAGGED_FACT_MAX_AGE_YEARS` is drawn with its own "FY…" tag (news-v23 phase 3)."""
    rows = [(f"Z{a}", f"Company {a}", f"Line {a}", 0.5, years[i % len(years)]) for i, a in enumerate("ABCDEFGHJ")]
    p = T._theme_parts(_theme_at(rows, run_date), run_date)
    lines = {t["name"]: t.get("line") for t in p.image_spec["tiles"]}
    fy_of = {n: fy for _s, n, _g, _sh, fy in rows}
    untagged = {fy_of[n] for n, ln in lines.items() if ln and ", FY" not in ln}
    tagged = {fy_of[n] for n, ln in lines.items() if ln and f", FY{fy_of[n]}" in ln}
    assert untagged == kept and tagged == set(years) - kept, (untagged, tagged)
    assert p.image_spec["subtitle"].endswith("fiscal " + " or ".join(sorted(kept)))


def test_a_fact_older_than_four_fiscal_years_is_never_drawn():
    rows = [(s, n, g, sh, "2022" if i == 0 else "2026") for i, (s, n, g, sh, _fy) in enumerate(_members(8))]
    assert compose(_theme_at(rows, THEME_RUN), THEME_RUN)["image_spec"]["tiles"][0]["line"] == (
        "Cloud, FY2022 · 50% of revenue")                                 # 2026 − 4: still drawn, tagged
    rows[0] = (rows[0][0], rows[0][1], "Cloud", 0.5, "2021")
    out = compose(_theme_at(rows, THEME_RUN), THEME_RUN)
    assert "Company 00" not in [t["name"] for t in out["image_spec"]["tiles"]][:7]   # no fact: drawn after the facts
    assert "2021" not in json.dumps(out)


def test_a_theme_with_no_fact_that_can_still_be_the_latest_is_refused():
    rows = [(s, n, g, sh, "2025") for s, n, g, sh, _fy in _members(8)]
    assert compose(_theme_at(rows, date(2027, 5, 31)), date(2027, 5, 31))["image_spec"]["subtitle"] == (
        "8 companies · each company's largest business and its share of total revenue · fiscal 2025")
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(_theme_at(rows, date(2027, 6, 1)), date(2027, 6, 1))
    assert e.value.code == "too_few_rows"


def test_every_theme_fact_surface_states_its_fiscal_year():
    """Review round 16 (low): every fact surface says its year — a narration pair joins two facts of ONE fiscal year
    and says it, its card keeps the year, every caption paragraph says its fact's year, and the picture's sub line says
    the fiscal year(s) of the untagged facts its tiles draw (a tagged tile says its own). news-v23 phase 3: the caption
    is one paragraph per company."""
    for rec in (ai_chips(), _theme_at(_PROBE_ROWS, THEME_RUN), theme(_members(8), theme_size=8),
                theme([(s, n, g, sh, "2025" if i % 2 else "2026") for i, (s, n, g, sh, _fy) in enumerate(_members(8))])):
        out = compose(rec, THEME_RUN)
        fy_of = {m.company.name: m.fiscal_year for m in rec.members}
        fact_lines = out["video_script"][:-1]
        if out["video_script"][-2] == T.LEXICON["th.explainer"]:
            fact_lines = fact_lines[:-1]
        for line, card in zip(fact_lines, out["cards"]):
            years = re.findall(r"\bfiscal (\d{4})\b", line)
            assert len(years) == 1, line                                  # one year a line: a pair shares it
            assert re.search(rf"(?i)\bfiscal {years[0]}\b", card["body"]), (line, card)
        for para in out["captions"]["instagram"].split("\n\n")[1:]:
            name = para.split(":", 1)[0]
            if name in fy_of and fy_of[name] and " is also in " not in para:
                assert re.search(rf"\bfiscal {fy_of[name]}\b", para), para
        tiles = out["image_spec"]["tiles"]
        drawn = sorted({fy_of[t["name"]] for t in tiles if "line" in t and ", FY" not in t["line"]})
        assert out["image_spec"]["subtitle"].endswith("fiscal " + " or ".join(drawn)), out["image_spec"]["subtitle"]
        for t in tiles:
            if "line" in t and ", FY" in t["line"]:
                assert f", FY{fy_of[t['name']]}" in t["line"]
        assert T.validate_package(out, record=rec) == []
    # alternate years: no two neighbours share one, so no line pairs them
    mixed = compose(theme([(s, n, g, sh, "2025" if i % 2 else "2026") for i, (s, n, g, sh, _fy) in
                           enumerate(_members(8))]), THEME_RUN)
    assert all(", and " not in line for line in mixed["video_script"])
    assert mixed["cards"][0] == {"title": "Company 00", "body": "Largest revenue line in fiscal 2026: Cloud"}
    # one year: pairs, each saying it
    same = compose(theme(_members(8), theme_size=8), THEME_RUN)
    assert same["video_script"][0] == "Company 00's largest revenue line in fiscal 2026 was Cloud, and Company 01's was Cloud."
    assert same["cards"][0] == {"title": "Company 00 and Company 01", "body": "Fiscal 2026: Cloud · Cloud"}
    for entry_id in ("th.pair.poss.poss", "th.pair.poss.of", "th.pair.of.poss", "th.pair.of.of", "th.mm.p.share",
                     "th.mm.p.seg", "th.c.pair.body", "th.c.pair.body.min", "th.c.one.body", "th.c.one.body.min",
                     "th.mm.sub.tail", "th.alt.segs", "th.alt.segs.each"):
        assert "fiscal {fy}" in T.LEXICON[entry_id].lower() or "fiscal {fys}" in T.LEXICON[entry_id], entry_id
    for entry_id in ("th.img.seg.fy", "th.mm.s.share", "th.mm.s.seg"):
        assert "FY{fy}" in T.LEXICON[entry_id], entry_id


#: One must-reject sample per STAKE_CLAIM_ROWS row — it matches its own row and no other — and an honest twin
#: no row matches (the investee is the stake fixture's Nscale, masked like any record name).
STAKE_CLAIM_ROW_SAMPLES = [
    ("Contoso led the round, which values Nscale at $90 billion.",
     "NVIDIA values the partnership with Nscale, it said in 2025."),
    ("Nscale is the largest AI startup in Europe.", "The largest tranche closed on Oct 8, 2025."),
    ("Nscale is the largest in Europe.", "The largest of the two tranches closed on Oct 8, 2025."),
]


def test_every_stake_claim_row_has_its_own_must_reject_sample_and_an_honest_twin():
    assert len(STAKE_CLAIM_ROW_SAMPLES) == len(T.STAKE_CLAIM_ROWS)
    rx = T._mask_re(["NVIDIA", "Nscale"])
    for i, (bad, twin) in enumerate(STAKE_CLAIM_ROW_SAMPLES):
        hits = [j for j, row in enumerate(T.STAKE_CLAIM_ROWS) if re.search(row, fold(T._masked(bad, rx)), re.I)]
        assert hits == [i], (bad, hits)
        assert not T.STAKE_CLAIM_RE.search(fold(T._masked(twin, rx))), twin


@pytest.mark.parametrize("text, names", [
    # the finding's three, verbatim
    ("Contoso led the round, which values Relecloud at $90 billion.", ["Contoso", "Relecloud"]),
    ("Relecloud is the largest AI startup in Europe.", ["Contoso", "Relecloud"]),
    ("Relecloud is the fastest-growing cloud company.", ["Contoso", "Relecloud"]),
    ("NVIDIA led a round valuing Nscale at about $3 billion.", ["NVIDIA", "Nscale"]),
    ("Nscale is the world's biggest AI chipmaker.", ["NVIDIA", "Nscale"]),
    ("Nscale is the leading AI cloud provider in Europe.", ["NVIDIA", "Nscale"]),
    ("Nscale is the top AI lab in Europe.", ["NVIDIA", "Nscale"]),
    ("Nscale is the largest among its peers.", ["NVIDIA", "Nscale"]),
])
def test_a_stake_background_never_values_or_ranks_a_company(text, names, monkeypatch):
    """Review round 16 (low, filter gap CONFIRMED by probe): `_stake_background` kept a present-tense valuation by
    a verb and a record / rank claim about a named company — none of BANNED_COPY, FORECAST_COPY, NEWS_BANNED_RE,
    STAKES_BANNED_RE or scan_text caught them (scan_text's valuation rows know "valued at" / "valuation" only) —
    and the kept text is the first caption paragraph of a company_stakes post (rules §1 "Companies": never a
    verdict, record or worth claim). STAKE_CLAIM_RE drops them. The anti-vacuity half: with it emptied, every
    other rule keeps each one — the gap these rows close."""
    assert T._stake_background(text, names) is None
    monkeypatch.setattr(T, "STAKE_CLAIM_RE", re.compile(r"(?!)"))
    assert T._stake_background(text, names) == text


def test_no_catalogue_background_is_dropped_by_the_stake_claim_rows(monkeypatch):
    """Over-block check against the real catalogue (`data/trillion_club_seed.json`): the claim rows match no
    background, and every background `_stake_background` kept before them it still keeps."""
    seed = json.loads((_BACKEND / "data" / "trillion_club_seed.json").read_text(encoding="utf-8"))
    names = {c["slug"]: c["display_name"] for c in seed["companies"]}
    rows = [(s["background"], [names.get(s["company_slug"]), s["investee_name"]]) for s in seed["stakes"]
            if isinstance(s.get("background"), str)]
    assert len(rows) >= 100
    claim = re.compile("|".join(f"(?:{r})" for r in T.STAKE_CLAIM_ROWS), re.IGNORECASE)
    assert [b for b, n in rows if claim.search(fold(T._masked(b, T._mask_re(n))))] == []
    with_rows = [T._stake_background(b, n) for b, n in rows]
    monkeypatch.setattr(T, "STAKE_CLAIM_RE", re.compile(r"(?!)"))
    assert [T._stake_background(b, n) for b, n in rows] == with_rows
    assert sum(k is not None for k in with_rows) >= 50


def test_validation_refuses_a_stake_string_that_values_or_ranks_a_company():
    """Defense in depth: `validate_package` (compose and `revalidate`) scans every stake string with the claim
    rows too, so a stored package edited to carry one is refused."""
    rec = nvidia_stakes()
    out = compose(rec, STAKES_RUN)
    assert T.validate_package(out, record=rec) == []
    for text in ("Intel is the largest chipmaker in America.", "The deal values Intel at $90 billion."):
        bad = copy.deepcopy(out)
        bad["captions"]["linkedin"] += "\n\n" + text
        assert ("captions.linkedin", "series_word") in {(v["field"], v["code"]) for v in T.validate_package(bad)}
    for entry_id, text in T.LEXICON.items():
        if entry_id.startswith("st."):
            assert not T.STAKE_CLAIM_RE.search(fold(text)), entry_id


@pytest.mark.parametrize("unit", ["values a ", "values at ", "valued at about ", "valuing x at $9 ", "largest ",
                                  "the largest a ", "most valuable ", "largest in ", "$9 at ", "makers "])
def test_the_stake_claim_rows_scan_in_linear_time(unit):
    from test_marketing_content_r2_overblock import _SWEEP_FLOOR, _SWEEP_LADDER, _SWEEP_LIMIT, assert_linear

    def make(n):
        text = (unit * (n // len(unit) + 1))[:n]
        return lambda: list(T.STAKE_CLAIM_RE.finditer(text))

    assert_linear(make, ("STAKE_CLAIM_RE", unit), floor=_SWEEP_FLOOR, ladder=_SWEEP_LADDER, limit=_SWEEP_LIMIT)


# ── news-v17: review round 17 (2026-10-10) ────────────────────────────────────

#: A stake string that says WHO reported, carried, recorded or measured the figure: the investor's name (or a
#: pronoun for it) before a reporting verb and "its", or the investor's own accounts. A catalogue row's source
#: can be another filer's document, so only the source may be credited with the figure.
_FILER_CLAIM_RE = re.compile(
    r"(?i)\b(?:reported|reports|carried|carries|recorded|records|measured|measures|marked|valued|values)\s+its\b"
    r"|\binvestor'?s'?\s+(?:own\s+)?(?:accounts|books|balance\s+sheet)\b")


def _offered_stake(rec):
    """Every string the stake builder OFFERS (hooks, headlines, titles, paragraphs, cards, alt), picked or not."""
    p = _parts(rec, STAKES_RUN)
    rows = [x for row in p.image_spec["sections"][0]["rows"] for x in (row["label"], *row["cells"])]
    return [*p.hook, *(s for line in p.lines for s in line), *(s for card in p.cards for s in card),
            *p.image_headlines, *p.video_headlines, *p.youtube_titles, *p.image_paragraphs, *p.video_paragraphs,
            *p.alt_paragraphs, p.alt_title, p.opening["headline"], p.opening["chip"], p.image_spec["title"], *rows]


def bhe_source_stake(**kw):
    """Review round 17's shape on a PUBLIC investee (news-v23: the catalogue's own Iroquois row is private, so never a
    post): the investor is Berkshire Hathaway, the figure is ANOTHER registrant's — Berkshire Hathaway Energy's 10-Q."""
    base = dict(stake_id="6d8f0a2c-4e6a-4c8e-9a1c-3e5a7c9e1b0e", investor=co("BRK-B", "Berkshire Hathaway"),
                investee_name="Fabrikam Pipeline", investee=co("FBPL", "Fabrikam Pipeline"), kind="us_listed_off_13f",
                value_usd=597_000_000.0, value_basis="carrying_value", ownership_pct=50.0, as_of=date(2026, 6, 30),
                source_title="Berkshire Hathaway Energy 10-Q (quarter ended Jun 30, 2026)",
                background="Held by BHE's Eastern Energy Gas; the $597M was BHE's consolidated carrying value.")
    base.update(kw)
    return stake(**base)


@pytest.mark.parametrize("factory", [
    bhe_source_stake, lambda: berkshire_mixed().stakes[0], tesla_spacex, lambda: berkshire_mixed().stakes[1],
    # the ".of" twins (an investor whose name is already possessive)
    lambda: stake(investor=co("MCO", "Moody's"), investee_name="Fabrikam Analytics",
                  investee=co("FBKA", "Fabrikam Analytics"), value_usd=3_200_000_000.0, value_basis="carrying_value",
                  kind="us_listed_off_13f"),
    lambda: stake(investor=co("MCO", "Moody's"), investee_name="Fabrikam Analytics",
                  investee=co("FBKA", "Fabrikam Analytics"), value_usd=3_200_000_000.0, value_basis="fair_value",
                  kind="us_listed_off_13f"),
], ids=["bhe_source", "litware_fair", "spacex_fair", "fabrikam_carrying", "of_twin_carrying", "of_twin_fair"])
def test_a_stake_figure_is_never_attributed_to_the_investor(factory):
    """Review round 17 (low, CONFIRMED by probe): the carrying- and fair-value headlines said "{inv} reported its
    {ee} stake at …" / "{inv} carried its {ee} stake at …" and L2 said "… in the investor's accounts". The
    catalogue's Berkshire Hathaway Energy rows (`data/trillion_club_seed.json`) sit under the investor Berkshire
    Hathaway, but their figure is a separate SEC registrant's — BHE's 10-Q — so X, Bluesky, Threads, TikTok,
    Facebook and LinkedIn read "Berkshire Hathaway reported its Iroquois Gas Transmission System stake at a
    carrying value of $597 million", contradicted by the background right after it ("the $597M was BHE's
    consolidated carrying value"). news-v17: the headline names no filer, the caption cites the source and L2
    says "the source says". Every OFFERED variant is checked, picked or not, and the composed package too.
    Mutation-checked (2026-10-10, in memory): restoring "{inv} reported its …" in `st.h.carrying_value.poss` /
    `st.h.fair_value.poss`, "{inv} carried its …" in `st.h.carrying_value.short.poss`, or "the investor's
    accounts" in `st.l2.carrying_value` each turned this red."""
    rec = factory()
    out = compose(rec, STAKES_RUN)
    inv = re.escape(rec.investor.name)
    for s in [*_offered_stake(rec), *_strings(out)]:
        assert not _FILER_CLAIM_RE.search(s), s
        assert not re.search(rf"\b{inv}\s+(?:reported|carried|recorded|measured|disclosed|said)\b", s), s
    if rec.value_basis == "carrying_value":
        assert out["video_script"][1] == ("Carrying value is the amount the source says the stake was recorded at "
                                          "on that date.")
    else:
        assert out["video_script"][1] == ("Fair value is the amount the source says the stake was measured at on "
                                          "that date.")
    # the long caption headlines credit the source by name
    source = rec.source_title
    assert out["captions"]["linkedin"].split("\n", 1)[0].endswith(f"Source: {source}."), out["captions"]["linkedin"]
    # news-v23: in a post of several stakes too — each row's paragraph credits its OWN source
    two = stakes(rec, intel_stake() if rec.investor.symbol == "NVDA" else
                 replace(intel_stake(), investor=rec.investor, stake_id="7b9d1f3a-5c7e-4a9b-8d1f-3a5c7e9b1d0c"),
                 investor=rec.investor)
    out2 = compose(two, STAKES_RUN)
    for s in [*_offered_stake(two), *_strings(out2)]:
        assert not _FILER_CLAIM_RE.search(s), s
        assert not re.search(rf"\b{inv}\s+(?:reported|carried|recorded|measured|disclosed|said)\b", s), s
    assert f"Source: {source}." in out2["captions"]["linkedin"]


def test_the_iroquois_probe_reads_as_the_sources_figure(monkeypatch):
    """The finding's own probe (review round 17), on a PUBLIC twin (news-v23: the catalogue's Iroquois row is private
    — never posted): the investor Berkshire Hathaway, the source BHE's 10-Q. The headline is source-neutral and the
    caption names BHE's 10-Q. news-v19: the background is a caption paragraph only while STAKE_BACKGROUND_IN_CAPTIONS is
    on (off by default), so the probe runs with it on, the state in which a background could contradict the headline."""
    assert "Source: Berkshire Hathaway Energy 10-Q (quarter ended Jun 30, 2026)." in (
        compose(bhe_source_stake(), STAKES_RUN)["captions"]["linkedin"])
    monkeypatch.setattr(T, "STAKE_BACKGROUND_IN_CAPTIONS", True)
    out = compose(bhe_source_stake(), STAKES_RUN)
    probe = "Berkshire Hathaway reported its Fabrikam Pipeline stake at a carrying value of $597 million"
    assert probe not in json.dumps(out)
    assert out["hook"] == "Berkshire Hathaway's Fabrikam Pipeline stake had a carrying value of $597 million."
    for platform in ("threads", "linkedin", "facebook", "tiktok", "instagram", "youtube_description"):
        assert out["captions"][platform].startswith(
            "Berkshire Hathaway's Fabrikam Pipeline stake had a carrying value of $597 million, as "
            "of Jun 30. Source: Berkshire Hathaway Energy 10-Q (quarter ended Jun 30, 2026)."), platform
    # the background still says whose figure it is, and no line contradicts it now
    assert "\n\nHeld by BHE's Eastern Energy Gas; the $597M was BHE's consolidated carrying value." in (
        out["captions"]["linkedin"])


def test_no_stake_entry_credits_the_investor_with_the_figure():
    """The lexicon half: no `st.*` entry puts a reporting verb after the investor's slot or names the investor's
    accounts — only the investor's OWN actions follow `{inv}` ("invested", "committed up to")."""
    for entry_id, text in T.LEXICON.items():
        if not entry_id.startswith("st."):
            continue
        assert not _FILER_CLAIM_RE.search(text), entry_id
        for m in re.finditer(r"\{inv\}\s+([a-z]+)", text):
            assert m.group(1) in ("invested", "committed", "in", "and", "had", "was"), (entry_id, m.group(1))
        assert "investor" not in text.lower(), entry_id


@pytest.mark.parametrize("row, alt, narrated", [
    (GME, "It shows $74.4M in 2 purchases, 3M shares in total.",
     "They report 2 purchases, 3 million shares in total, for $74.4 million."),
    (buy("CTSO", "Contoso", "director", None, 1_000_000.0, 1.4, 3, [date(2026, 11, 12), date(2026, 11, 13)]),
     "It shows $1M in 3 purchases, about 1 share in total.",
     "They report 3 purchases, about 1 share in total, for $1 million."),
    (buy("CTSO", "Contoso", "director", None, 640_000.0, 2_600.0, 3, [date(2026, 11, 12)]),
     "It shows $640K in 3 purchases, 2,600 shares in total.",
     "3 purchases are reported, 2,600 shares in total, for $640,000."),
    (buy("NKE", "Nike", "cfo", None, 1_250_000.0, 18_500.0, 1, [date(2026, 11, 10)]),
     "It shows $1.3M in 1 purchase of about 19K shares.",
     "It reports one purchase of about 19,000 shares, for $1.3 million."),
], ids=["two_purchases_3M", "three_purchases_about_1", "neutral_one_filing_day", "one_purchase_keeps_of"])
def test_the_spotlight_alt_text_states_the_share_total(row, alt, narrated):
    """Review round 17 (low, CONFIRMED by probe): compose(week(GME)) gave the alt text "It shows $74.4M in 2
    purchases of 3M shares." — read as two purchases of 3M shares each (6M) — while the narration said "They
    report 2 purchases, 3 million shares in total". The record's share figure is the row's TOTAL over all its
    purchase lines (round 15 reworded the roundup's alt, `ins.alt.rows`, for the same reason). news-v17:
    `ins.alt.spot.many` says "in total"; one purchase keeps "of" (`ins.alt.spot.one`), as the narration does.
    Mutation-checked (2026-10-10, in memory): restoring "{k_txt} of {shares_s}" in `ins.alt.spot.many` turned
    the multi-purchase cases red."""
    # owner decisions 2026-11-20: the one-company picture (and its "It shows …" alt text) is gone — a roundup's alt text
    # lists each buyer's TOTAL (`ins.alt.rows`); the lead row's narration still states its share total
    out = compose(week(row, series="insider_buys" if row.role != "ceo" else "ceo_buys"), MON)
    assert alt not in json.dumps(out["image_post"]) and "each buyer's total" in out["image_post"]["paragraphs"][0]
    assert out["video_script"][1] == narrated
    if row.purchases > 1:
        assert not re.search(r"\b\d+ purchases of\b", json.dumps(out["image_post"])), out["image_post"]


def test_the_post_commit_sweep_wordings_hold():
    """Post-commit sweep (2026-10-10): (1) a theme never says HOW Caydex groups companies — the rotation
    weighs signals §1 forbids naming, so "based on each company's business" was false; (2) Money Map's rest
    bar can hold revenue the breakdown never attributes, so it never reads as a plain reported "Other";
    (3) a carrying value is dated like its sibling bases, never an undated present-tense value.

    The review of 2026-10-16 (live preview F6) on (2): the rest ALSO holds lines the company does break out that
    the post cannot name (a code, a label too long to draw, a sixth line — Alphabet's "Google subscriptions,
    platforms, and devices" sat in a $49.6B rest), so "not broken out" was false for them. It says "Everything
    else" now — true of both, and still never a plain "Other"."""
    lex = T.LEXICON
    for key in ("th.grouping", "th.c.grouping.body"):
        text = lex[key].casefold()
        assert "based on" not in text and "business" not in text, (key, lex[key])
    assert lex["mm.bar.other"] == "Everything else" and lex["mm.item.other"] == "everything else, {value}"
    for key in ("mm.bar.other", "mm.item.other"):
        assert "not broken out" not in lex[key] and lex[key].split(",")[0].casefold() != "other", key
    assert "on that date" in lex["st.c2.carrying_value.body"]
    assert "in the accounts" not in lex["st.c2.carrying_value.body"]


# ── news-v19: the members-elect window (owner decision 4, 2026-10-15) ─────────


def _people(rec):
    """A Form 4 record's people: a roundup's rows, every person of the insider list's rows (owner decisions
    2026-11-20)."""
    return tuple(p for c in rec.companies for p in c.people) if isinstance(rec, R.InsiderSpotlight) else rec.rows


def _without_names(rec):
    if isinstance(rec, R.InsiderSpotlight):
        return replace(rec, companies=tuple(
            R.InsiderSpotlightCompany(lead=replace(c.lead, person_name=None),
                                      others=tuple(replace(r, person_name=None) for r in c.others))
            for c in rec.companies))
    return replace(rec, rows=tuple(replace(r, person_name=None) for r in rec.rows))


def _name_forms(rec):
    """Every way a record's persons could be written: the full name, first + last, the surname alone."""
    out = set()
    for r in _people(rec):
        if r.person_name:
            toks = fold(r.person_name).split()
            out |= {" ".join(toks), f"{toks[0]} {toks[-1]}", toks[-1]}
    return out


def _says(text, form):
    return re.search(r"(?<![a-z0-9])" + re.escape(form) + r"(?![a-z0-9])", text) is not None


@pytest.mark.parametrize("factory", [ceo_roundup, lambda: week(GME), insider_mixed],
                         ids=["roundup", "spotlight", "insider_mixed"])
def test_inside_the_members_elect_window_a_form4_post_names_no_one(monkeypatch, factory):
    """Owner decision 4: Mon Nov 16, 2026 is inside the window (Election Day Nov 3, 2026 through Jan 3, 2027).
    A NAMED record composes role-only — byte-identical to the same record with no names, no name, first +
    last or surname in any string — and passes validate_package and revalidate (the re-check recomposes on
    the same run date). Mutation-checked (2026-10-15): `names_ok` without its window term turned it red."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    rec = factory()
    forms = _name_forms(rec)
    assert forms                                                            # anti-vacuity: someone is named
    out = compose(rec, MON)
    assert out["persons"] == []
    text = fold(json.dumps(out, ensure_ascii=False))
    assert not [f for f in forms if _says(text, f)]
    assert out == compose(_without_names(rec), MON)
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []
    assert T.revalidate(json.loads(json.dumps(out)), fact_sheet=sheet(rec), run_date=MON) == []
    # control: the same record outside the window names its person in the body paragraphs
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)
    named = compose(rec, MON)
    assert named["persons"] and named != out
    assert any(_says(fold(json.dumps(named, ensure_ascii=False)), f) for f in forms)


@pytest.mark.parametrize("run_date, named", [
    # Review of 2026-10-16 (ME-1): judged over every day a run's posts can go out — the run date and the two
    # after it (`R.members_elect_for_run`): a post composed on the Monday before Election Day can be approved on
    # Tuesday night or resent on Wednesday, so the Sunday and Monday runs are role-only too.
    (date(2026, 10, 31), True),      # its posts go out Oct 31 .. Nov 2: before Election Day (Tue Nov 3, 2026)
    (date(2026, 11, 1), False),
    (date(2026, 11, 2), False),      # the Monday before Election Day (named before the review)
    (date(2026, 11, 3), False),      # Election Day: the window opens
    (date(2026, 12, 31), False),
    (date(2027, 1, 3), False),       # the new Congress is sworn in: the last day of the window
    (date(2027, 1, 4), True),
    (date(2027, 11, 2), True),       # an odd year's Election Day: no federal general election, no window
    (date(2028, 11, 4), True),       # Election Day 2028 is Tue Nov 7 (Nov 1 is a Wednesday)
    (date(2028, 11, 5), False),
    (date(2028, 11, 7), False),
], ids=["oct_31", "nov_1", "nov_2_monday_before", "election_day", "december", "jan_3", "jan_4", "odd_year",
        "2028_nov_4", "2028_nov_5", "2028"])
def test_the_window_boundaries_decide_the_names(monkeypatch, run_date, named):
    """Mutation-checked by hand (2026-10-16): `_insider_parts` reading `members_elect_window(run_date)` again
    turned nov_1 / nov_2_monday_before / 2028_nov_5 red (named)."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    start, end = R.insider_window(run_date)
    row = buy("GME", "GameStop", "ceo", "Ryan Cohen", 74_400_000.0, 3_000_000.0, 2,
              [end - timedelta(days=4), end - timedelta(days=3)])
    rec = week(row, start=start, end=end)
    out = compose(rec, run_date)
    assert (out["persons"] == ["Ryan Cohen"]) is named
    assert _says(fold(json.dumps(out, ensure_ascii=False)), "ryan cohen") is named
    assert T.validate_package(out, record=rec) == []
    assert T.revalidate(json.loads(json.dumps(out)), fact_sheet=sheet(rec), run_date=run_date) == []


def test_the_validator_refuses_a_name_inside_the_window(monkeypatch):
    """Defence in depth: an in-window output that names a person — in `persons`, or in a caption body while
    `persons` is empty (a path that bypassed `person()`) — is a `placement` violation; outside the window the
    same body paragraph breaks no placement rule (A7 allows a name there). Mutation-checked (2026-10-15):
    removing the window block in `_validate` turned it red."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    rec = week(GME)
    out = compose(rec, MON)
    listed = copy.deepcopy(out)
    listed["persons"] = ["Ryan Cohen"]
    assert ("persons", "placement") in {(v["field"], v["code"]) for v in T.validate_package(listed, record=rec)}
    body = copy.deepcopy(out)
    body["captions"]["linkedin"] += "\n\nThe filings name Ryan Cohen, the CEO of GameStop."
    assert ("captions.linkedin", "placement") in {(v["field"], v["code"])
                                                   for v in T.validate_package(body, record=rec)}
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)
    assert not [v for v in T.validate_package(body, record=rec) if v["code"] == "placement"]


def test_the_validator_judges_the_same_days_as_the_composer(monkeypatch):
    """Review of 2026-10-16 (ME-1): the validator uses the composer's own run check (`members_elect_for_run`):
    an output of Mon Nov 2, 2026 — outside the window itself, but its posts can go out on Election Day — composes
    role-only, passes validate_package and revalidate (compose and re-check agree), and a name written into a
    caption body of it is a `placement` violation. Mutation-checked by hand (2026-10-16): `_validate` reading
    `members_elect_window(run_date)` let the named body through on Nov 2 (red)."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    run_date = date(2026, 11, 2)
    assert not R.members_elect_window(run_date) and R.members_elect_for_run(run_date)
    start, end = R.insider_window(run_date)
    rec = week(buy("GME", "GameStop", "ceo", "Ryan Cohen", 74_400_000.0, 3_000_000.0, 2,
                   [end - timedelta(days=4), end - timedelta(days=3)]), start=start, end=end)
    out = compose(rec, run_date)
    assert out["persons"] == [] and not _says(fold(json.dumps(out, ensure_ascii=False)), "ryan cohen")
    assert T.validate_package(out, record=rec) == []
    assert T.revalidate(json.loads(json.dumps(out)), fact_sheet=sheet(rec), run_date=run_date) == []
    body = copy.deepcopy(out)
    body["captions"]["linkedin"] += "\n\nThe filings name Ryan Cohen, the CEO of GameStop."
    assert ("captions.linkedin", "placement") in {(v["field"], v["code"]) for v in T.validate_package(body, record=rec)}


def test_a_one_share_purchase_never_says_shares_above_one_share():
    """INS-1 (review of 2026-10-16): a director's one-share BRK-A purchase (about $712K) passes every gate. Owner
    decisions 2026-11-20: its roundup's headline is number-neutral about shares ("… disclosed buying their own
    company's stock"), its narration says "one purchase of 1 share", and on the insider list its detail line reads
    "1 share · … a share, as filed" — never "1 shares"."""
    rec = week(buy("BRK-A", "Berkshire Hathaway", "director", None, 712_000.0, 1.0, 1, [date(2026, 11, 12)]),
               series="insider_buys")
    out = compose(rec, MON)
    assert T.validate_package(out, record=rec) == []
    for text in (out["opening_card"]["headline"], out["image_spec"]["title"], out["image_post"]["title"]):
        assert not re.search(r"\bshares\b", text), text
    assert out["video_script"][1] == "It reports one purchase of 1 share, for $712,000."
    assert not re.search(r"\b1 shares\b", json.dumps(out))
    assert "ins.open.head" not in T.LEXICON


def test_inside_the_window_a_members_row_is_still_dropped_never_shown_role_only(monkeypatch):
    """The window turns names OFF; it never turns the Congress drop off: a member's row (filed under a legal
    name) is dropped — not shown as "A director of Fox" — and the week goes on with the other rows (three left: owner
    decisions 2026-11-20)."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    member = buy("FOX", "Fox", "director", "Joshua S. Gottheimer", 900_000.0, 30_000.0, 1, [date(2026, 11, 12)])
    other = buy("HSY", "Hershey", "director", None, 150_000.0, 812.0, 1, [date(2026, 11, 14)])
    end = date(2026, 11, 15)
    out = compose(week(member, other, week_filler(0, "insider_buys", end, 120_000.0),
                       week_filler(1, "insider_buys", end, 110_000.0), series="insider_buys"), MON)
    assert [r["key"] for r in out["logo_refs"]] == ["HSY", "WGB", "WTT"]
    assert "Fox" not in json.dumps(out) and out["persons"] == []


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_the_members_elect_window_changes_nothing_but_form4_names(monkeypatch, name, factory, run_date):
    """news-v19 sweep: the window (forced ON here, whatever the sample's date) withholds Form 4 NAMES and
    nothing else. Every other series composes byte-identically to the same day outside it; a Form 4 sample
    composes exactly as its record with every name removed. Both still pass validate_package (with and
    without the record) and revalidate after a JSONB round trip — so no series is refused from Election Day
    through January 3 by the window check in `_validate` (only Form 4 parts ever fill `persons`)."""
    rec = factory()
    form4 = rec.series in T.FORM4_PERSON_SERIES                 # news-v22: the insider spotlight's people too
    expected = compose(_without_names(rec) if form4 else rec, run_date)       # outside the window
    monkeypatch.setattr(R, "members_elect_window", lambda day: True)
    inside = compose(rec, run_date)
    assert inside == expected
    assert inside["persons"] == []
    assert T.validate_package(inside, record=rec) == [] and T.validate_package(inside) == []
    assert T.revalidate(_jsonb(inside), fact_sheet=_jsonb(sheet(rec)), run_date=run_date) == []
    if form4:
        text = fold(json.dumps(inside, ensure_ascii=False))
        assert not [f for f in _name_forms(rec) if len(f.split()) > 1 and _says(text, f)]


def test_the_window_sweep_is_not_vacuous():
    """Anti-vacuity for the sweep above: most Form 4 samples name someone outside the window (two are
    role-only by construction), and each of those differs once the window withholds the name."""
    named = [(n, f, d) for n, f, d in SAMPLES if f().series in T.FORM4_PERSON_SERIES and _name_forms(f())]
    assert len(named) >= 4 and "isp_named_outside_window" in {n for n, _f, _d in named}
    for _n, f, d in named:
        assert compose(f(), d) != compose(_without_names(f()), d)


# ── news-v19 (Batch 1, plan item 5): names and wording ────────────────────────────────────────────
#
# The live preview of 2026-10-14 and its verifier (wf_355df13b-cc8) found, all cosmetic, all public: a single
# Form 4 headline naming its company twice ("Accelevation Holdings' CEO disclosed buying $3.7 million of
# Accelevation Holdings stock"), "Part of the shares" beside the caption's "Some of the shares", "At least 1
# more", a descriptive investee without its article ("As of that date, Louisiana data center venture was not
# listed …", narrated, so burned into the video), the catalogue's background fragments pasted into captions,
# and covers whose chip was a ticker the logo already shows while the figure carried no date.


#: The single-row Form 4 samples: (record, the company's display name).
_ONE_ROW = [
    (lambda: week(GME), "GameStop"),
    (lambda: week(LOW), "Lowe's"),
    (lambda: week(buy("NKE", "Nike", "cfo", None, 1_250_000.0, 18_500.0, 1, [date(2026, 11, 10)]),
                  series="insider_buys"), "Nike"),
    (insider_late, "Hershey"),
    (lambda: week(buy("ACCV", "Accelevation Holdings", "ceo", "Michael Rubiera", 3_700_000.0, 205_000.0, 1,
                      [date(2026, 11, 13)])), "Accelevation Holdings"),
]


@pytest.mark.parametrize("factory, co_name", _ONE_ROW, ids=["gme", "lowes_of_twin", "nike_cfo", "hershey_director",
                                                            "accelevation"])
def test_a_single_row_form4_headline_names_its_company_once(factory, co_name):
    """The subject already names the company ("GameStop's CEO", "The CEO of Lowe's", "A director of Hershey"), so
    every single-row headline — X / Bluesky / Threads whole, every caption's first line, the YouTube title, the
    cover and the spotlight image — names it ONCE and says "of the company's stock" / "disclosed buying stock".
    Mutation-checked by hand (2026-10-15): restoring "of {co} stock" in `ins.ch.one.long.one` /
    `ins.yt.long`, or "{co} stock" in `ins.open.head`, turned this red."""
    # owner decisions 2026-11-20: a Form 4 post is always a roundup — the lead row's headline leads its VIDEO captions
    # and its YouTube title (the picture's and the cover's are the roundup's own)
    rec = factory()
    out = compose(rec, MON)
    assert T.validate_package(out, record=rec) == []
    heads = [out["captions"]["youtube_title"]]
    heads += [out["captions"][f].split("\n", 1)[0] for f in ("tiktok", "instagram", "youtube_description")]
    for head in heads:
        assert head.count(co_name) == 1, head
        assert f"{co_name} stock" not in head.replace(f"{co_name}'s", ""), head
    assert "of the company's stock" in out["captions"]["tiktok"].split("\n", 1)[0]
    assert out["captions"]["youtube_title"].endswith(" of the company's stock")


def test_some_of_the_shares_and_at_least_one_more_are_words():
    """"Some of the shares" in the narration and on the card, as the caption says; the lower bound's "one" is a
    word on the line, the card and the video paragraph — still a bound ("At least …"), never "One more"."""
    lead = buy("CTSO", "Contoso", "director", None, 640_000.0, 2_600.0, 1, [date(2026, 11, 12)], holding="mixed")
    other = buy("HSY", "Hershey", "director", None, 150_000.0, 812.0, 1, [date(2026, 11, 14)])
    rec = week(lead, other, series="insider_buys")
    out = compose(rec, MON)
    assert out["video_script"][2] == "Some of the shares are reported as held indirectly."
    assert out["cards"][2] == {"title": "Partly held indirectly", "body": "Some of the shares, per the filing"}
    # owner decisions 2026-11-20: a roundup is three companies or more, so its bound is "At least 2 more" at the least
    # (the "one more" entries left the lexicon with the two-company roundup)
    assert out["video_script"][3] == ("At least 2 more directors disclosed buying their own company's stock in the "
                                      "same week.")
    assert out["cards"][3]["body"] == "At least 2 more directors disclosed buying their own company's stock"
    assert ("At least 2 more directors disclosed buying their own company's stock in Form 4s filed the same week."
            in out["captions"]["youtube_description"])
    assert not {"ins.l4.more.one", "ins.c4.more.one", "ins.vp.more.one"} & set(T.LEXICON)
    assert T.validate_package(out, record=rec) == []
    for entry_id, text in T.LEXICON.items():
        assert not re.search(r"\bAt least 1\b", text), entry_id
        assert not re.search(r"\bPart of the shares\b", text), entry_id


# the covers' chips ─────────────────────────────────────────────────────────────────────────────────

#: (sample, the cover's chip) — every series: a ticker where the cover shows the company's own logo (a one-company
#: Form 4, a listed 13F filer, a stakes investor, news-v23: the Money Map, whose fiscal year rides the cover's footer),
#: none on a list cover without a plate (a Form 4 roundup, Congress, earnings, an unlisted 13F filer, a theme).
_COVER_CHIPS = [
    # news-v23 (2026-11-20): a Form 4 ROUNDUP's cover is its picture's list — no plate, no chip
    ("ceo_roundup5", None), ("insider_mixed", None), ("berkshire_13f", "BRK-B"),
    # news-v23: an unlisted filer's 13F list cover has NO chip (no plate: owner, "else no plate"; no quarter either)
    # news-v23 (2026-11-20): the Money Map cover's chip is the TICKER, as on the picture's plate
    ("pershing_13f", None), ("costco_money_map", "COST"),
    # news-v23: a Congress Count and an earnings post are LISTS (2-3 / 2-4 companies): their covers draw the list, no
    # plate, no chip
    ("rivian_money_map", "RIVN"), ("congress_accenture", None),
    # news-v23: a stakes post is a list too — its cover keeps the INVESTOR's plate, so its chip is its ticker
    ("stakes_nvidia", "NVDA"), ("stake_spacex_fair_value", "TSLA"), ("earnings_loss", None),
    ("earnings_profit", None), ("theme_ai_chips", None),
]


@pytest.mark.parametrize("name, chip", _COVER_CHIPS, ids=[c[0] for c in _COVER_CHIPS])
def test_every_cover_chip(name, chip):
    """The chip each cover draws under its logo (`opening_card.chip`), within OPENING_CHIP_MAX_CHARS and among
    the strings the server lets the worker draw. Mutation-checked by hand (2026-10-15): dating an earnings cover
    by `period_end or report_date` turned `earnings_profit` red ("Quarter ended Nov 10, 2026" — a report day
    is no quarter's end), and the old ticker chips turned the Money Map and Pershing rows red (news-v23, 2026-11-20:
    the Money Map's chip is its ticker again — the picture's plate — and its year rides the cover's footer)."""
    factory, run_date = next((f, d) for n, f, d in SAMPLES if n == name)
    out = compose(factory(), run_date)
    card = out["opening_card"]
    assert card.get("chip") == chip
    if chip is not None:
        assert len(chip) <= T.OPENING_CHIP_MAX_CHARS
        assert chip in onscreen.opening_strings(card, _logo_entries(out))
        assert onscreen.validate_opening_card(card, {r["key"] for r in out["logo_refs"]}) is None


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_no_sample_cover_chip_is_over_the_cap(name, factory, run_date):
    chip = compose(factory(), run_date)["opening_card"].get("chip")
    assert chip is None or 1 <= len(chip) <= T.OPENING_CHIP_MAX_CHARS, (name, chip)


def test_a_period_chip_is_the_quarter_or_year_the_figure_is_for():
    """news-v23: no cover chip dates a figure any more — an earnings cover is its table (the picture's subtitle dates
    the reports), a 13F cover carries NO period (a filer WITH a symbol keeps its ticker beside its own logo, the plate;
    a filer without one no chip at all), and (2026-11-20) a Money Map cover's chip is the ticker while the record's own
    fiscal year ends the cover's short footer ("· fiscal 2024")."""
    amended = pershing(amended_on=date(2026, 11, 14))
    assert "chip" not in compose(amended, TUE)["opening_card"]
    q2 = berkshire(filer_symbol=None, period="2026-Q2", period_end=date(2026, 6, 30), filed_on=date(2026, 8, 14))
    assert "chip" not in compose(q2, date(2026, 8, 18))["opening_card"]
    assert compose(berkshire(), TUE)["opening_card"]["chip"] == "BRK-B"
    # news-v23: an earnings cover is its table — no chip (the picture's subtitle dates the reports)
    assert "chip" not in compose(earnings(period_end=date(2026, 8, 31)), EARNINGS_RUN)["opening_card"]
    assert compose(earnings(period_end=None), EARNINGS_RUN)["image_spec"]["subtitle"] == "Reported Nov 5, 2026"
    old_year = compose(costco(fiscal_year="2024", period_end=date(2024, 9, 1)), date(2024, 11, 21))
    assert old_year["opening_card"]["chip"] == "COST" and old_year["image_footer"].endswith("· fiscal 2024")


def test_a_stored_cover_chip_over_the_cap_or_edited_is_refused():
    """Defence in depth: `validate_package` refuses a stored cover whose chip is over the cap (the server's
    `template_onscreen` check), and `revalidate` refuses any edited chip (it recomposes)."""
    rec = earnings()
    out = compose(rec, EARNINGS_RUN)
    long_chip = copy.deepcopy(out)
    long_chip["opening_card"]["chip"] = "Quarter ended Sep 30, 2026 · reported Nov 5"
    assert ("opening_card", "opening_card_invalid") in {(v["field"], v["code"])
                                                        for v in T.validate_package(long_chip, record=rec)}
    ticker = copy.deepcopy(out)
    ticker["opening_card"]["chip"] = "NWFT"
    assert ("opening_card", "field_mismatch") in {
        (v["field"], v["code"]) for v in T.revalidate(_jsonb(ticker), fact_sheet=_jsonb(sheet(rec)),
                                                      run_date=EARNINGS_RUN)}


# a descriptive investee takes "the" ────────────────────────────────────────────────────────────────


def louisiana(**kw):
    """The catalogue's Meta row (`data/trillion_club_seed.json`, sort_order 3): a DESCRIPTIVE investee name — what is
    held, not a company's name — at a carrying value from Meta's 10-Q. news-v23 (owner 2026-11-19 "public only"): it is
    private, so it is never a post; the article rule is proved below on a LISTED twin (a row the record can carry)."""
    base = dict(stake_id="3a5c7e9b-1d3f-4b5d-8f7a-9c1e3a5b7d09", investor=co("META", "Meta"),
                investee_name="Louisiana data center venture", investee=co("LDCV", "Louisiana data center venture"),
                kind="us_listed_off_13f", value_usd=2_920_000_000.0, value_basis="carrying_value", ownership_pct=20.0,
                as_of=date(2026, 6, 30), source_title="Meta 10-Q (quarter ended Jun 30, 2026)",
                background="Partners committed pro rata to about $27B of costs; Meta signed leases on the campus.")
    base.update(kw)
    return stake(**base)


_VENTURE = "Louisiana data center venture"


def test_the_catalogues_private_venture_is_never_a_post():
    """news-v23: the catalogue's own row (no listing, kind private) cannot stand in a post at all."""
    with pytest.raises(ValueError):
        R.CompanyStakes(series="company_stakes", investor=co("META", "Meta"),
                        stakes=(louisiana(investee=None, kind="private"),))


def test_a_descriptive_investee_takes_the_in_running_text_never_on_a_label():
    """The verifier's probe (2026-10-14), composed (news-v23: on a listed twin): every running-text place says "the …"
    — the narration (burned captions), the captions — while the picture's row keeps the name as drawn, and "{inv_s}
    {ee} stake" (a determiner already there) stays bare. Mutation-checked by hand (2026-10-15): with
    `takes_name_article` answering False, and with `ee_fills` ignoring it, this turned red."""
    rec = louisiana()
    out = compose(rec, STAKES_RUN)
    assert out["video_script"][2] == f"Shares of the {_VENTURE} are listed on a U.S. exchange."
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][0] == _VENTURE    # a drawn label: the name as is
    assert out["hook"] == f"Meta's {_VENTURE} stake had a carrying value of $2.9 billion."
    assert f"\n\nShares of the {_VENTURE} are listed on a U.S. exchange." in out["captions"]["linkedin"]
    every = list(_strings(out))
    for s in every:
        for m in re.finditer(re.escape(_VENTURE), s):
            before = s[:m.start()]
            assert before.endswith(("the ", "Meta's ")) or before == "", (s, before[-20:])
        assert "the the" not in s.lower() and f"The {_VENTURE} stake" not in s, s
    assert T.validate_package(out, record=one(rec)) == []
    assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(one(rec))), run_date=STAKES_RUN) == []


@pytest.mark.parametrize("kw, hook, l3", [
    (dict(value_basis="invested"), f"Meta invested $2.9 billion in the {_VENTURE}.",
     f"Shares of the {_VENTURE} are listed on a U.S. exchange."),
    (dict(kind="commitment", value_basis="committed_up_to"), f"Meta committed up to $2.9 billion to the {_VENTURE}.",
     "A commitment is money agreed to be invested, which may be paid over time."),
    (dict(value_basis="invested", listed_since=date(2025, 3, 3)), f"Meta invested $2.9 billion in the {_VENTURE}.",
     f"The {_VENTURE} has been listed on an exchange since March 2025."),
], ids=["invested", "committed_up_to", "listed_since_opens_the_sentence"])
def test_every_descriptive_form_reads_with_its_article(kw, hook, l3):
    rec = louisiana(**kw)
    out = compose(rec, STAKES_RUN)
    assert out["hook"] == hook and out["video_script"][2] == l3
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][0] == _VENTURE
    assert T.validate_package(out, record=one(rec)) == []
    # in a post of several, the narration's "also on the list" names it with its article too
    two = compose(stakes(intel_stake(), replace(rec, investor=co("NVDA", "NVIDIA"))), STAKES_RUN)
    assert f"the {_VENTURE}" in " ".join(two["video_script"]) or "carried at" in " ".join(two["video_script"])


@pytest.mark.parametrize("name", ["Nscale", "SK hynix", "ONE Store Korea", "Louisiana Data Center Venture"])
def test_a_company_name_takes_no_article(name):
    """A company's own name — capitalised, or styled in lower case on purpose ("SK hynix") — reads as it is."""
    out = compose(stake(investee_name=name), STAKES_RUN)
    assert out["video_script"][2] == f"Shares of {name} are listed on a U.S. exchange."
    assert f"the {name}" not in json.dumps(out) and f"The {name}" not in json.dumps(out)


# ══ Batch 3 (2026-10-20): the EXTRA video post's caption, re-checked by the template rules ═══════════════════
#
# Facebook / LinkedIn / Threads may also post the day's video (owner decision 2, 2026-10-15): its caption is the
# platform's own accepted caption under the narrated-video disclaimer (`post_copy.extra_video_caption`), and
# `extra_video_problems` re-checks it as a public string of the output — the word scan, the placement rule (no
# person in its first line or its code-owned suffix; the whole caption on Threads) and the members-elect window
# — when the extras are frozen and again at create_posts. compose / revalidate / COMPARED_FIELDS / the version
# are untouched by it.

_EXTRA_FIELDS = post_copy.EXTRA_VIDEO_FIELDS


def _video_caption(out, platform, run_date):
    return post_copy.extra_video_caption(platform, out["posts"][platform]["caption"], run_date, T.AUTHORSHIP)


_DERIVE = object()


def _problems(out, rec, run_date, platform, caption=_DERIVE, *, draws_picture=True):
    """The re-check as create_posts runs it on a video that DOES draw the day's picture (the data card) — the
    rules these tests are about; the B3-LIVE-1 picture rule has its own tests below (`draws_picture=False`)."""
    caption = _video_caption(out, platform, run_date) if caption is _DERIVE else caption
    return T.extra_video_problems(out, fact_sheet=sheet(rec), run_date=run_date, platform=platform, caption=caption,
                                  video_draws_picture=draws_picture)


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_batch3_extra_video_problems_pass_for_every_series_fixture(name, factory, run_date):
    rec = factory()
    checked = 0
    for state, allow in (("live", False), ("preorder", True), ("prelaunch", False)):
        out = compose(rec, run_date, state, allow)
        for platform in _EXTRA_FIELDS:
            if platform not in out["posts"]:
                continue
            caption = _video_caption(out, platform, run_date)
            assert caption.endswith(post_copy.disclaimer_for(platform, run_date, "template", video=True))
            # news-v23 (the 13F list): a cover that leaves rows to the caption ("+N more changes in the caption") needs
            # them in this caption — Threads carries them when they fit its 500 bytes, else only the picture's rest, and
            # its extra is then `cover_caption`
            # (news-v23, company stakes: a promised row may be kept by its SHORT form too — `video_promise_short`)
            parts = _parts(rec, run_date)
            shorts = parts.video_promise_short
            kept = [p in caption or (i < len(shorts) and shorts[i] in caption)
                    for i, p in enumerate(parts.video_promise)]
            cover = ([{"field": f"video_posts.{platform}", "code": "cover_caption", "detail": ""}]
                     if parts.video_promise and not all(kept) else [])
            assert platform == "threads" or not cover, (name, platform)
            got = [dict(p, detail="") if p["code"] == "cover_caption" else p
                   for p in _problems(out, rec, run_date, platform, caption)]
            assert got == cover, (name, platform, got)
            # as JSONB hands them back: the stored output and fact sheet re-check the same
            again = T.extra_video_problems(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=run_date,
                                           platform=platform, caption=caption, video_draws_picture=True)
            assert [dict(p, detail="") if p["code"] == "cover_caption" else p for p in again] == cover
            if cover:
                checked += 1
                continue
            # B3-LIVE-1: on a video that does NOT draw the picture only a Form 4 ROUNDUP is refused (its caption
            # speaks of the picture's rows); every other sample's caption suits any video
            plain = _problems(out, rec, run_date, platform, caption, draws_picture=False)
            roundup = rec.series in ("ceo_buys", "insider_buys") and len(rec.rows) >= 2
            assert plain == ([{"field": f"video_posts.{platform}", "code": "picture_caption", "detail": plain[0]["detail"]}]
                             if roundup else []), (name, plain)
            checked += 1
    assert checked >= 6, name     # anti-vacuity: Facebook / LinkedIn (and Threads) are really re-checked


# B3-LIVE-1 / B3-FR-1 (2026-10-20): a Form 4 ROUNDUP's Facebook / LinkedIn / Threads caption is built from the PICTURE
# parts — "The largest of these: …", "The purchases: <every row>", "More purchases: …" (news-v20: format-neutral, they
# ride text posts too; before, "The largest here" / "The purchases shown" / "Also shown") — while the video names only
# the lead row's company (cards, narration, the opening card's logo) and the others by role ("At least 2 more CEOs …").
# Such a caption may ride the video only when the video draws the picture (the data card replaces "The purchases").


@pytest.mark.parametrize("factory", [ceo_roundup, insider_mixed], ids=["ceo_roundup", "insider_mixed"])
@pytest.mark.parametrize("platform", _EXTRA_FIELDS)
def test_batch3_a_roundups_extra_needs_the_picture_in_its_video(factory, platform):
    """Mutation-checked (2026-10-20): with the check removed (or `picture_bound` never set) both rows turn red."""
    rec = factory()
    out = compose(rec, MON)
    caption = _video_caption(out, platform, MON)
    if platform != "threads":
        # the defect itself: the caption lists rows the video never shows — it names the other companies (news-v20
        # words them format-neutrally, "The purchases: …", but the rows are still the picture's alone)
        assert "The purchases: " in caption and all(r.company.name in caption for r in rec.rows), caption
        assert not any(r.company.name in " ".join(out["video_script"]) for r in rec.rows[1:])
    refused = _problems(out, rec, MON, platform, caption, draws_picture=False)
    assert [(v["field"], v["code"]) for v in refused] == [(f"video_posts.{platform}", "picture_caption")]
    # never the caption's text: no company of the record in the detail
    assert "picture" in refused[0]["detail"] and not any(r.company.name in refused[0]["detail"] for r in rec.rows)
    # only exactly True says the video draws it: a truthy stand-in is no answer (fail closed)
    for not_true in (1, "yes", None):
        assert [v["code"] for v in _problems(out, rec, MON, platform, caption, draws_picture=not_true)] == [
            "picture_caption"]
    assert _problems(out, rec, MON, platform, caption, draws_picture=True) == []
    # the default (a caller that does not say) is the refusal
    assert [v["code"] for v in T.extra_video_problems(out, fact_sheet=sheet(rec), run_date=MON, platform=platform,
                                                      caption=caption)] == ["picture_caption"]


@pytest.mark.parametrize("platform", _EXTRA_FIELDS)
def test_batch3_every_form4_week_is_picture_bound(platform):
    """Owner decisions 2026-11-20 ("3 - 5 companies at once"): there is no one-row Form 4 post any more — a week about
    one lead row is a roundup of three, its picture captions list rows the video never shows, so its extra video needs
    the picture in the video (the data card)."""
    rec = week(GME)
    out = compose(rec, MON)
    assert T._picture_bound(rec, T.SERIES_SPECS[rec.series], MON) is True
    assert [v["code"] for v in _problems(out, rec, MON, platform, draws_picture=False)] == ["picture_caption"]
    assert _problems(out, rec, MON, platform, draws_picture=True) == []


def test_batch3_only_a_form4_roundup_is_picture_bound():
    bound = {name for name, factory, run_date in SAMPLES
             if T._picture_bound(factory(), T.SERIES_SPECS[factory().series], run_date)}
    assert bound == {name for name, factory, _d in SAMPLES if factory().series in ("ceo_buys", "insider_buys")}
    assert {"ceo_roundup", "insider_mixed"} <= bound           # anti-vacuity
    # never part of the composed output: compose / revalidate / COMPARED_FIELDS are untouched
    assert "picture_bound" not in compose(ceo_roundup(), MON)


def _widest_roundup(n: int = R.INSIDER_MAX_ROWS):
    """The widest Form 4 roundup a record admits, for the caption budgets: ``n`` rows of a 32-character company name
    and a 40-character person, a CFO among directors ("company insiders", the longest group noun), billion-dollar
    amounts, every row LATE (the longest Form 4 paragraph: the trade dates) and both flags (indirect, amended)."""
    rows = []
    for i in range(n):
        name = f"Northwind Traders Holdings {'ABCDEFGH'[i]}corp"       # 32 characters, words of at most 8
        person = f"Bartholome{'stuvwxyz'[i]}s Q. Featherstone-Worthington"  # 40 characters, distinct people
        assert len(name) == R.COMPANY_NAME_CHARS[1] and len(person) == R.PERSON_NAME_MAX
        rows.append(R.InsiderPurchase(
            company=co(f"NW{'ABCDEFGH'[i]}", name), role="cfo" if i == 0 else "director", person_name=person,
            amount_usd=4_987_654_321.0 - i * 5e8, shares=123_456_789.0, purchases=9_999,
            earliest_trade_date=date(2026, 11, 9) - timedelta(days=R.INSIDER_MAX_FILING_LAG_DAYS),
            latest_trade_date=date(2026, 11, 13), filing_dates=(date(2026, 11, 9), date(2026, 11, 13)),
            holding="indirect" if i % 2 else "direct", amended=True))
    return week(*rows, series="insider_buys")


@pytest.mark.parametrize("n", range(R.INSIDER_MIN_ROWS, R.INSIDER_MAX_ROWS + 1))
def test_news_v20_a_roundups_captions_are_true_with_or_without_the_picture(n):
    """news-v20 (the carried open question of the round-3 review, 2026-10-20): a Form 4 roundup's caption parts ride
    TEXT posts too — X is text only, and Facebook / LinkedIn / Threads / Bluesky post text when no picture goes out —
    so no caption of any platform says "shown" or "here". The headline's superlative is scoped ("The largest of
    these") to the rows counted, and on Facebook / LinkedIn the row paragraphs ("The purchases: …", then "More
    purchases: …" past three rows) travel together and name every row: the widest roundup a record admits keeps both
    (its trailing flag and Form 4 paragraphs are what a budget drops first). Mutation-checked (2026-10-20, in
    memory): the news-v19 `ins.p.rows` / `ins.p.rows2` / `ins.ch.many.long` turn the first assertion red."""
    for rec in (ceo_roundup(), insider_mixed(), _widest_roundup(n)):
        out = compose(rec, MON)
        for field, body in out["captions"].items():
            assert not re.search(r"\b(?:shown|here)\b", body, re.IGNORECASE), (field, body)
        for field in ("facebook", "linkedin"):
            body = out["captions"][field]
            head = body.split("\n", 1)[0]
            assert head.endswith(f"The largest of these: {rec.rows[0].company.name}, "
                                 f"{T.money_words(rec.rows[0].amount_usd)}.") or "The largest" not in head, head
            # owner decisions 2026-11-20: "The purchases: <the picture's rows>", "More purchases: <the rest>" — every
            # row while the budget allows, and ALWAYS the rows the picture leaves out (its "+N more" promise)
            assert ("More purchases: " in body) is (len(rec.rows) > T.INS_PICTURE_ROWS), body
            assert all(r.company.name in body for r in rec.rows[T.INS_PICTURE_ROWS:]), body
            if len(rec.rows) <= T.INS_PICTURE_ROWS:
                assert "The purchases: " in body and all(r.company.name in body for r in rec.rows), body
    # the widest roundup a record admits (five rows since the final review of 2026-11-26 — every one drawn) keeps EVERY
    # row, its moved note and the Form 4 paragraph on Facebook: nothing is left off the picture, so no "More purchases"
    widest = _widest_roundup()
    wide = compose(widest, MON)["captions"]["facebook"]
    assert len(widest.rows) == R.INSIDER_MAX_ROWS == T.INS_PICTURE_ROWS
    assert "The purchases: " in wide and "More purchases: " not in wide and "held indirectly" in wide
    assert all(r.company.name in wide for r in widest.rows) and "A Form 4 is the report" in wide


def test_batch3_a_ceo_named_in_the_body_paragraphs_passes():
    """A7 allows a person in the long captions' body paragraphs (never their first line): the Facebook / LinkedIn
    video caption keeps that body word for word — and still passes."""
    rec = week(GME)
    out = compose(rec, MON)
    assert out["persons"] == ["Ryan Cohen"]
    for platform in ("facebook", "linkedin"):
        caption = _video_caption(out, platform, MON)
        head, rest = caption.split("\n", 1)
        assert "Cohen" not in head and "Ryan Cohen" in rest
        assert _problems(out, rec, MON, platform, caption) == []
    assert "Cohen" not in _video_caption(out, "threads", MON)        # the Threads caption is headline-only


def _named(out, platform):
    """The output with "Ryan Cohen" at the head of `platform`'s caption body AND its composed post, kept consistent
    so the video caption still re-derives and only the first-line rule can see it."""
    out["captions"][platform] = "Ryan Cohen " + out["captions"][platform]
    out["posts"][platform]["caption"] = "Ryan Cohen " + out["posts"][platform]["caption"]
    return out


def _in_suffix(out, platform):
    """The name inside the code-owned SUFFIX (just before the disclaimer) — the caption still ends with its
    disclaimer once, so it re-derives; only the suffix rule can see it."""
    disc = post_copy.disclaimer_for(platform, MON, "template")
    cap = out["posts"][platform]["caption"]
    out["posts"][platform]["caption"] = cap.replace("\n\n" + disc, "\n\nRyan Cohen\n\n" + disc)
    assert out["posts"][platform]["caption"] != cap
    return out


@pytest.mark.parametrize("platform, tamper, field", [
    ("facebook", _named, "video_posts.facebook[0]"),
    ("linkedin", _named, "video_posts.linkedin[0]"),
    ("facebook", _in_suffix, "video_posts.facebook.suffix"),
    ("linkedin", _in_suffix, "video_posts.linkedin.suffix"),
    ("threads", _in_suffix, "video_posts.threads"),          # the whole Threads caption is restricted
], ids=["facebook_first_line", "linkedin_first_line", "facebook_suffix", "linkedin_suffix", "threads"])
def test_batch3_extra_video_problems_refuse_a_person_in_the_first_line_or_suffix(platform, tamper, field):
    rec = week(GME)
    out = tamper(compose(rec, MON), platform)
    found = {(v["field"], v["code"]) for v in _problems(out, rec, MON, platform)}
    assert (field, "placement") in found, found


@pytest.mark.parametrize("platform", _EXTRA_FIELDS)
@pytest.mark.parametrize("word", ["Smart money moves.", "It soared.", "Insiders bought."])
def test_batch3_extra_video_problems_refuse_a_banned_word_in_the_caption(platform, word):
    rec = ceo_roundup()
    out = compose(rec, MON)
    out["posts"][platform]["caption"] = f"{word} " + out["posts"][platform]["caption"]
    codes = {(v["field"], v["code"]) for v in _problems(out, rec, MON, platform)}
    assert any(f == f"video_posts.{platform}.caption" and c in ("banned_word", "off_table_verb") for f, c in codes), codes
    # the untouched output's video caption is clean: the word is what was found
    assert _problems(compose(rec, MON), rec, MON, platform) == []


def test_batch3_extra_video_problems_members_elect_window_refuses_a_named_person(monkeypatch):
    """A named Form 4 record composed outside the window (its body names the CEO) whose video caption is re-checked
    for a run date inside it (Mon Nov 16, 2026): `_withheld_name_hits` refuses the name in the video caption —
    nobody is named anywhere in the window. Outside the window the same caption passes (A7 allows the body)."""
    rec = week(GME)
    out = compose(rec, MON)                                     # the autouse fixture: the window is off
    assert out["persons"] == ["Ryan Cohen"]
    caption = _video_caption(out, "facebook", MON)
    assert _problems(out, rec, MON, "facebook", caption) == []
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    assert R.members_elect_for_run(MON)
    found = {(v["field"], v["code"]) for v in _problems(out, rec, MON, "facebook", caption)}
    assert ("video_posts.facebook.caption", "placement") in found, found
    # the in-window composition names no one, and its video caption passes inside the window
    inside = compose(rec, MON)
    assert inside["persons"] == [] and _problems(inside, rec, MON, "facebook") == []


@pytest.mark.parametrize("platform", _EXTRA_FIELDS)
def test_batch3_extra_video_problems_refuse_a_caption_that_is_not_the_derivation(platform):
    rec = ceo_roundup()
    out = compose(rec, MON)
    good = _video_caption(out, platform, MON)
    assert _problems(out, rec, MON, platform, good) == []
    ai = post_copy.disclaimer_for(platform, MON, "ai", video=True)
    for label, caption in [
        ("the text post's own caption", out["posts"][platform]["caption"]),
        ("one character more", good + "."),
        ("another body", good.replace(out["captions"][platform], "Another body.", 1)),
        ("the ai narrated disclaimer",
         good[:len(good) - len(post_copy.disclaimer_for(platform, MON, "template", video=True))] + ai),
        ("None", None),
    ]:
        codes = {(v["field"], v["code"]) for v in _problems(out, rec, MON, platform, caption)}
        assert codes == {(f"video_posts.{platform}", "caption_invalid")}, (label, codes)
    # a platform that takes no extra post, or an output whose primary post is missing: caption_invalid
    for other in ("x", "bluesky", "tiktok", "instagram"):
        codes = {v["code"] for v in _problems(out, rec, MON, other, good)}
        assert codes == {"caption_invalid"}, other
    gone = copy.deepcopy(out)
    del gone["posts"][platform]
    assert {v["code"] for v in T.extra_video_problems(gone, fact_sheet=sheet(rec), run_date=MON, platform=platform,
                                                      caption=good)} == {"caption_invalid"}
    # another run day: the long disclaimer is dated, so a Facebook / LinkedIn caption does not re-derive; the short
    # one is undated (the output's own run date is `revalidate`'s to check, and create_posts runs it first)
    other_day = {v["code"] for v in T.extra_video_problems(out, fact_sheet=sheet(rec), run_date=MON + timedelta(days=1),
                                                           platform=platform, caption=good)}
    assert ("caption_invalid" in other_day) is (platform != "threads"), other_day


@pytest.mark.parametrize("output, fact_sheet, run_date, platform, caption, code", [
    (None, "SHEET", MON, "facebook", "c", "schema"),
    ([], "SHEET", MON, "facebook", "c", "schema"),
    ("text", "SHEET", MON, "facebook", "c", "schema"),
    ("OUT", None, MON, "facebook", "c", "facts_invalid"),
    ("OUT", {}, MON, "facebook", "c", "facts_invalid"),
    ("OUT", "SHEET", "2026-11-16", "facebook", "c", "provenance"),
    ("OUT", "SHEET", None, "facebook", "c", "provenance"),
    ("OUT", "OTHER_SHEET", MON, "facebook", "c", "provenance"),            # another series' record
    ("NO_POSTS", "SHEET", MON, "facebook", "c", "caption_invalid"),
    ("NO_PERSONS", "SHEET", MON, "facebook", "VIDEO", "schema"),           # unreadable: logged, never a pass
    ("OUT", "SHEET", MON, None, "c", "caption_invalid"),
    ("OUT", "SHEET", MON, ["facebook"], "c", "caption_invalid"),
], ids=["none", "list", "text", "no_sheet", "empty_sheet", "date_string", "no_date", "other_series", "no_posts",
        "no_persons", "no_platform", "list_platform"])
def test_batch3_extra_video_problems_never_raise(output, fact_sheet, run_date, platform, caption, code, caplog):
    rec = ceo_roundup()
    out = compose(rec, MON)
    outputs = {"OUT": out, "NO_POSTS": {k: v for k, v in out.items() if k != "posts"},
               "NO_PERSONS": {k: v for k, v in out.items() if k != "persons"}}
    sheets = {"SHEET": sheet(rec), "OTHER_SHEET": sheet(costco())}
    got = T.extra_video_problems(outputs.get(output, output) if isinstance(output, str) else output,
                                 fact_sheet=sheets.get(fact_sheet, fact_sheet) if isinstance(fact_sheet, str)
                                 else fact_sheet,
                                 run_date=run_date, platform=platform,
                                 caption=_video_caption(out, "facebook", MON) if caption == "VIDEO" else caption)
    assert got and {v["code"] for v in got} == {code}, got
    assert all(set(v) == {"field", "code", "detail"} and len(v["detail"]) <= 200 for v in got)


def test_batch3_extra_video_problems_is_exported_and_composes_nothing_new():
    assert "extra_video_problems" in T.__all__
    rec = ceo_roundup()
    out = compose(rec, MON)
    before = copy.deepcopy(out)
    for platform in _EXTRA_FIELDS:
        _problems(out, rec, MON, platform)
    assert out == before                          # the probe never writes into the output
    # the extras' keys never disturb the output's own re-check (create_posts runs both)
    stored = _jsonb(out)
    stored["extra_post_formats"] = {"facebook": "video", "linkedin": "video"}
    stored["video_posts"] = {p: {"title": None, "caption": _video_caption(out, p, MON)} for p in ("facebook", "linkedin")}
    assert T.revalidate(stored, fact_sheet=_jsonb(sheet(rec)), run_date=MON) == []
    assert not {"extra_post_formats", "video_posts"} & set(T.COMPARED_FIELDS)


def test_the_logo_refs_read_a_feature_pictures_header_logo():
    """2026-11-15: the `feature` picture (the spotlight series) keeps its company in its `header` — the logo drawn in
    the art band — so its logo refs are that header logo, then the opening card's, in draw order (the spotlight /
    bars rule), each with its name; a referenced logo with no name refuses the output (image_spec_invalid)."""
    spec = {"layout": "feature", "header": {"logo": "CTSO", "name": "Contoso", "chip": "CTSO"}}
    names = {"CTSO": "Contoso", "MSFT": "Microsoft"}
    assert T._logo_refs(spec, {"logos": ["MSFT", "CTSO"]}, names) == [
        {"key": "CTSO", "name": "Contoso"}, {"key": "MSFT", "name": "Microsoft"}]
    with pytest.raises(T.NewsTemplateRefused):
        T._logo_refs(spec, {"logos": ["CTSO"]}, {"MSFT": "Microsoft"})


# ── the spotlight lane: the insider LIST (owner decisions 2026-11-20) and the 13F list ───────────────────────────
#
# Owner decisions of 2026-11-20 — "for insider trade, i think we discussed that there is no single company already, each
# post or each image/video will have more than 2,3, ideally are 3 - 5 companies at once."; "i pick option 1: 3-5"; the
# video cover "All rows, compact (Recommended)". The insider spotlight is a LIST of 3-8 companies (five drawn, the rest
# in the caption); the filing's own cost per share is shown ONLY in the captions and on the picture rows' grey detail
# lines ("Captions + picture", 2026-11-15), always "as filed", never the word "price"; a ticker in brackets opens each
# company's caption paragraph; names only in body text, never inside the members-elect window.

_ISP_SAMPLES = [s for s in SAMPLES if s[0].startswith("isp_")]
_F13S_SAMPLES = [s for s in SAMPLES if s[0].startswith("f13s_")]
#: news-v23: every 13F spotlight is a LIST (two or more holdings; the single-holding post is gone)
_F13S_LIST_SAMPLES = list(_F13S_SAMPLES)
#: Everything a VIDEO or the picture's ALT TEXT carries (never a cost per share): owner decision "Captions + picture".
_NO_COST_FIELDS = ("hook", "video_script", "cards", "opening_card", "image_post")


def _cost_strings(rec):
    """Every way the record's per-share costs could be written: each person's lowest and highest, as filed."""
    out = set()
    for p in _people(rec):
        for v in (p.cost_per_share_min_usd, p.cost_per_share_max_usd):
            out.add(T.cost_image(v))
    return out


#: Seven companies of SAMPLE figures (each amount = shares × a cost inside the filed range), largest total first.
_ISP7 = [
    ("UBER", "Uber", "ceo", 2_001_000.0, 28_000.0, (71.20, 71.80)),
    ("CMG", "Chipotle", "cfo", 1_202_400.0, 24_000.0, (50.10, 50.10)),
    ("STZ", "Constellation Brands", "director", 760_500.0, 5_200.0, (145.90, 146.60)),
    ("C", "Citigroup", "director", 540_000.0, 5_200.0, (103.70, 104.20)),
    ("D", "Dominion Energy", "ceo", 309_987.2, 5_308.0, (58.40, 58.40)),
    ("NKE", "Nike", "director", 250_000.0, 3_500.0, (71.20, 71.60)),
    ("HSY", "Hershey", "ceo", 200_000.0, 1_250.0, (159.80, 160.20)),
]


def isp_n(n, *, day=date(2026, 11, 18), start=date(2026, 11, 17), end=date(2026, 11, 18)):
    """The first `n` of _ISP7 as an insider list filed on `day` (its trades the same day), read on SPOT_THU."""
    rows = [isp_company(ip(sym, name, role, None, amt, shares, 2, [day], [day], cost))
            for sym, name, role, amt, shares, cost in _ISP7[:n]]
    return isp_list(*rows, start=start, end=end)


def _forged(rec, field_, rows):
    """`rec` with `field_` set to `rows` WITHOUT the record's own checks (a hand-edited fact sheet, a future bug): the
    composer must refuse it on its own."""
    bad = copy.copy(rec)
    object.__setattr__(bad, field_, tuple(rows))
    return bad


def test_isp_the_samples_cover_every_shape():
    """Anti-vacuity for the tables below: a cluster (two others), a LATE lead, an indirect director with another
    director, a NAMED pair outside the members-elect window — each a list of three companies or more; every 13F kind, an
    amended filing, a listed filer (news-v23: its plate)."""
    assert {n for n, _f, _d in _ISP_SAMPLES} == {"isp_cluster", "isp_single", "isp_indirect",
                                                  "isp_named_outside_window"}
    recs = [f() for _n, f, _d in _ISP_SAMPLES]
    assert all(R.INSIDER_MIN_ROWS <= len(r.companies) <= R.INSIDER_MAX_ROWS for r in recs)
    assert any(len(r.companies[0].others) == 2 for r in recs)
    assert any(any(p.holding == "indirect" for p in _people(r)) for r in recs)
    assert any(p.person_name for r in recs for p in _people(r))
    lists = [f() for _n, f, _d in _F13S_LIST_SAMPLES]
    assert {len(r.moves) for r in lists} >= {2, 3, 5}
    assert any({m.move for m in r.moves} == set(R.THIRTEEN_F_MOVES) for r in lists)
    assert any(r.amended_on is not None for r in lists) and any(r.filer_symbol for r in lists)
    assert len(f13s_list8().moves) == R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES == 8            # 6 drawn + 2 in the caption
    assert {R.thirteen_f_spotlight_lists_every_change(r) for r in lists} == {True, False}
    # owner decisions 2026-11-20: both spotlight series draw a `rows` list — the `feature` picture is no series' now
    assert T.SERIES_SPECS["insider_spotlight"].layout == "rows"
    assert T.SERIES_SPECS["thirteen_f_spotlight"].layout == T.SERIES_SPECS["thirteen_f"].layout == "rows"
    assert not [sid for sid, spec in T.SERIES_SPECS.items() if spec.layout == "feature"]


@pytest.mark.parametrize("n", [3, 4, 5])
def test_isp_the_list_draws_every_company_up_to_five_with_its_detail_line(n):
    """insider_multi3.jpg / insider_multi5.jpg: the title "Insiders at {n} companies" + "disclosed buying stock", the grey
    "Total purchases" label, one row per company (logo, chip, name, roles, the total) with its grey detail line; the
    cover the SAME rows compact — no detail line (insider_multi5_cover_alt_all5.jpg) — and no "+N more"."""
    rec = isp_n(n)
    out = compose(rec, SPOT_THU)
    sec = out["image_spec"]["sections"][0]
    assert out["image_spec"]["title"] == out["opening_card"]["headline"] == (
        f"Insiders at {n} companies disclosed buying stock")
    assert sec["heading"] == out["opening_card"]["list"]["heading"] == "Total purchases"
    assert [r["logo"] for r in sec["rows"]] == [r[0] for r in _ISP7[:n]] == out["opening_card"]["logos"][:1] + [
        r[0] for r in _ISP7[1:n]]
    assert all(set(r) == {"logo", "chip", "cells", "detail"} for r in sec["rows"])
    assert [r["cells"][2] for r in sec["rows"]] == [T.money_image(x[3]) for x in _ISP7[:n]]
    cover = out["opening_card"]["list"]["rows"]
    assert cover == [{k: v for k, v in r.items() if k != "detail"} for r in sec["rows"]]
    assert "more" not in sec and "more" not in out["opening_card"]["list"]
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("n", [6, 7])
def test_final_review_six_or_more_companies_are_no_form4_record(n):
    """Owner decisions 2026-11-20 — the option he picked: "3 to 5 :: At least 3 companies, at most 5 (the 5 largest
    purchases when more qualify)". The build had kept up to eight (five drawn, the rest in the captions); the final
    review of 2026-11-26 holds it to five: NO Form 4 record — the insider list or the weekly roundup — admits six or
    more companies (the adapter keeps the five largest: test_marketing_company_news_adapter), so no post ever says six
    or more and no "+N more companies" line is drawn. Mutation-checked by hand: `INSIDER_MAX_ROWS = 8` let both
    records build."""
    assert R.INSIDER_MAX_ROWS == R.INSIDER_PICTURE_ROWS == T.INS_PICTURE_ROWS == 5
    with pytest.raises(ValueError, match="outside 3..5"):
        isp_n(n)
    with pytest.raises(ValueError, match="outside 3..5"):
        week(*CEO_ROWS7[:n])
    # the five largest of the same buyers ARE a post — every company drawn, no "+N more", one count everywhere
    out = compose(week(*CEO_ROWS7[:5]), MON)
    assert out["image_spec"]["title"] == out["opening_card"]["headline"] == (
        "At least 5 CEOs disclosed buying their own company's stock")
    assert [r["logo"] for r in out["image_spec"]["sections"][0]["rows"]] == ["GME", "LOW", "INTC", "SBUX", "NEE"]
    assert "more" not in out["image_spec"]["sections"][0] and "more" not in out["opening_card"]["list"]
    five = compose(isp_n(5), SPOT_THU)
    assert five["image_spec"]["title"] == "Insiders at 5 companies disclosed buying stock"
    assert "more" not in five["image_spec"]["sections"][0] and "more" not in five["opening_card"]["list"]


def test_isp_fewer_than_three_companies_is_refused_whatever_built_it():
    """Owner decisions 2026-11-20: never a one- or two-company insider post — the RECORD refuses fewer than
    INSIDER_MIN_ROWS (and more than INSIDER_MAX_ROWS), and the COMPOSER refuses a forged one on its own
    (`too_few_rows`), as it refuses a list a member of Congress's row leaves with two."""
    with pytest.raises(ValueError):
        isp_n(2)
    rec = isp_n(3)
    for k in (1, 2):
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(_forged(rec, "companies", rec.companies[:k]), SPOT_THU)
        assert e.value.code == "too_few_rows"
    nine = list(rec.companies) * 3
    with pytest.raises(ValueError):
        R.InsiderSpotlight(series="insider_spotlight", window_start=rec.window_start, window_end=rec.window_end,
                           companies=tuple(nine[:9]))
    # the weekly roundups: the same floor on the record and in the composer
    with pytest.raises(ValueError):
        week(GME, LOW, pad=False)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(_forged(ceo_roundup(), "rows", ceo_roundup().rows[:2]), MON)
    assert e.value.code == "too_few_rows"


@pytest.mark.parametrize("others, label, caption, spoken", [
    (0, "CEO", "the CEO disclosed", "by its chief executive."),
    (1, "CEO + at least one other insider", "the CEO and at least one other insider disclosed",
     "by its chief executive and at least one other insider."),
    (2, "CEO + at least 2 other insiders", "the CEO and at least 2 other insiders disclosed",
     "by its chief executive and at least 2 other insiders."),
    (4, "CEO + at least 4 other insiders", "the CEO and at least 4 other insiders disclosed",
     "by its chief executive and at least 4 other insiders."),
], ids=["alone", "one_other", "two_others", "four_others"])
def test_isp_roles_only_and_the_others_are_always_a_lower_bound(others, label, caption, spoken):
    """Who disclosed buying, by ROLE (never a name on a drawn or narrated string): the lead's role, and the others as a
    LOWER BOUND — the adapter's gates drop people and the roles read (CEO, CFO, director) are not every insider, so the
    data never proves a complete count ("CEO + at least 2 other insiders", as the one-issuer spotlight said "at least")."""
    lead = ip("CTSO", "Contoso", "ceo", None, 2_001_650.0, 14_000.0, 2, [date(2026, 11, 18)],
              [date(2026, 11, 17)], (142.10, 143.85))
    extra = [ip("CTSO", "Contoso", role, None, a, a / 100.0, 1, [date(2026, 11, 18)], [date(2026, 11, 17)],
                (100.0, 100.0)) for role, a in (("director", 900_000.0), ("cfo", 700_000.0), ("director", 500_000.0),
                                                ("director", 300_000.0))][:others]
    rec = spotlight(lead, *extra, start=date(2026, 11, 17), end=date(2026, 11, 18))
    out = compose(rec, SPOT_THU)
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][1] == label
    assert out["opening_card"]["list"]["rows"][0]["cells"][1] == label
    assert f"Contoso (CTSO): {caption} buying" in out["captions"]["facebook"]
    assert out["video_script"][0].endswith(spoken)
    for s in _strings(out):
        assert not re.search(r"\b[0-9]+ other insiders?\b", s.replace("at least ", "")) or "at least" in s, s


@pytest.mark.parametrize("cost, shares, amount, detail", [
    ((71.20, 71.80), 28_000.0, 2_001_000.0, "28K shares · $71.20–$71.80 a share, as filed"),
    ((50.10, 50.10), 24_000.0, 1_202_400.0, "24K shares · $50.10 a share, as filed"),
    ((4_998.75, 5_005.00), 1_000.0, 5_000_000.0, "1,000 shares · $4,998.75–$5,005.00 a share, as filed"),
    ((1_234.5, 1_234.5), 100.0, 123_450.0, "100 shares · $1,234.50 a share, as filed"),
    ((700_000.0, 700_000.0), 1.0, 700_000.0, "1 share · $700,000.00 a share, as filed"),
    ((142.1234, 143.8501), 14_000.0, 14_000.0 * 143.0, "14K shares · $142.1234–$143.8501 a share, as filed"),
], ids=["range", "single_price", "four_digit_range", "four_digit_single", "one_share", "four_places"])
def test_isp_the_detail_line_formats(cost, shares, amount, detail):
    """The grey detail line (insider_multi3.jpg): "<shares> shares · $<low>–$<high> a share, as filed" — ONE price when
    every line carries the same, every place the filing carries (`cost_image`, four at most), a four-digit price with
    its thousands comma, "1 share" for one; at most template_onscreen.ROW_DETAIL_MAX_CHARS. Never the word "price"."""
    lead = ip("CTSO", "Contoso", "ceo", None, amount, shares, 2, [date(2026, 11, 18)], [date(2026, 11, 18)], cost)
    out = compose(spotlight(lead, start=date(2026, 11, 17), end=date(2026, 11, 18)), SPOT_THU)
    row = next(r for r in out["image_spec"]["sections"][0]["rows"] if r["logo"] == "CTSO")
    assert row["detail"] == detail
    assert len(detail) <= onscreen.ROW_DETAIL_MAX_CHARS
    assert "price" not in json.dumps(out).lower()


@pytest.mark.parametrize("name, factory, run_date", _ISP_SAMPLES, ids=[s[0] for s in _ISP_SAMPLES])
def test_isp_the_cost_per_share_is_caption_and_picture_detail_only(name, factory, run_date):
    """Every row's filed cost per share is in the long captions (its paragraph, "… a share, as filed") and on the
    picture rows' DETAIL lines — and NOWHERE in the video (hook, narration, every card, the cover: the compact rows have
    no detail line) nor in the alt text or the YouTube title; no string says "price". Mutation-checked by hand
    (2026-11-20): a detail line left on the cover's rows turned this red."""
    rec = factory()
    out = compose(rec, run_date)
    costs = _cost_strings(rec)
    video = json.dumps({k: out[k] for k in _NO_COST_FIELDS}, ensure_ascii=False)
    assert not [c for c in costs if c in video], name
    assert not re.search(r"\ba share\b|\bas filed\b", video), name
    rows = out["image_spec"]["sections"][0]["rows"]
    picture_rest = json.dumps({**out["image_spec"], "sections": [{**out["image_spec"]["sections"][0], "rows": [
        {k: v for k, v in r.items() if k != "detail"} for r in rows]}]}, ensure_ascii=False)
    assert not [c for c in costs if c in picture_rest], name
    assert not re.search(r"\ba share\b|\bas filed\b", picture_rest), name
    assert all(r["detail"].endswith(" a share, as filed") for r in rows)
    assert not re.search(r"\bprice", json.dumps(out, ensure_ascii=False), re.IGNORECASE)
    for f in ("facebook", "linkedin", "instagram", "youtube_description"):
        assert out["captions"][f].count("a share, as filed") == len(rec.companies), (name, f)
    for f in ("youtube_title",):
        assert not [c for c in costs if c in out["captions"][f]], (name, f)


@pytest.mark.parametrize("name, factory, run_date", _ISP_SAMPLES, ids=[s[0] for s in _ISP_SAMPLES])
def test_isp_each_ticker_stands_in_brackets_in_its_paragraph_and_never_in_a_drawn_string(name, factory, run_date):
    """B decision 9: "(SYM)" beside each company's name in its caption paragraph (and the lead's in the long headline)
    — never a cashtag; the picture and the cover carry a ticker only as its row's chip."""
    rec = factory()
    out = compose(rec, run_date)
    for c in rec.companies:
        assert f"{c.company.name} ({c.company.symbol}):" in out["captions"]["facebook"], (name, c.company.symbol)
    assert not T._CASHTAG_RE.search(json.dumps(out["captions"]))
    assert f"({rec.companies[0].company.symbol})" not in out["captions"]["youtube_title"]
    drawn = [s for _f, s in T._drawn_strings(out["image_spec"], "image_spec") + T._drawn_strings(
        out["opening_card"], "opening_card")]
    assert [s for s in drawn if re.search(r"\([A-Z.-]{1,10}\)", s)] == []
    chips = [r["chip"] for r in out["image_spec"]["sections"][0]["rows"]]
    assert chips == [c.company.symbol for c in rec.companies[:5]]


@pytest.mark.parametrize("sym", ["BUY", "CUT", "SOLD", "EXIT"])
def test_isp_a_ticker_that_trips_a_banned_word_or_verb_drops_its_row(sym):
    """B decision 9 (fail closed): the ticker is our text in brackets, so a symbol the news ban or the verb table reads
    ("(BUY)", "(CUT)") is refused (`slot_rejected`) — the COMPOSER refuses the list, and the build's
    `drop_refused_rows` drops that one company before it composes (three left: the post stands; two: too few)."""
    rec = isp_n(4)
    bad_row = R.InsiderSpotlightCompany(lead=replace(rec.companies[1].lead, company=co(sym, "Chipotle")), others=())
    bad = replace(rec, companies=(rec.companies[0], bad_row) + rec.companies[2:])
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(bad, SPOT_THU)
    assert e.value.code == "slot_rejected", e.value
    narrowed = T.drop_refused_rows(bad, SPOT_THU)
    assert [c.company.symbol for c in narrowed.companies] == ["UBER", "STZ", "C"]
    compose(narrowed, SPOT_THU)
    with pytest.raises(T.NewsTemplateRefused) as e:
        T.drop_refused_rows(replace(bad, companies=bad.companies[:3]), SPOT_THU)
    assert e.value.code == "too_few_rows"


def test_isp_a_member_of_congress_never_leads_a_row_and_is_dropped_from_the_others():
    """A member of Congress is never in a post: a row LED by one is dropped (as a roundup drops its row — fewer than
    three left: `too_few_rows`); among a row's others that person is dropped and the count says one fewer (the adapter
    drops the whole person first)."""
    member = "Josh Gottheimer"                      # a sitting member in the committed roster (the roundup test's)
    assert R.is_congress_name(member)
    rec = isp_cluster()
    lead_row = rec.companies[0]
    named_other = R.InsiderSpotlightCompany(lead=lead_row.lead, others=(
        replace(lead_row.others[0], person_name=member), lead_row.others[1]))
    out = compose(replace(rec, companies=(named_other,) + rec.companies[1:]), SPOT_THU)
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"][1] == "CEO + at least one other insider"
    assert R.congress_name_hits(json.dumps(out)) == []
    four = isp_n(4)
    member_lead = R.InsiderSpotlightCompany(lead=replace(four.companies[1].lead, person_name=member), others=())
    out = compose(replace(four, companies=(four.companies[0], member_lead) + four.companies[2:]), SPOT_THU)
    assert "CMG" not in json.dumps(out["image_spec"]) and R.congress_name_hits(json.dumps(out)) == []
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(replace(rec, companies=(R.InsiderSpotlightCompany(
            lead=replace(lead_row.lead, person_name=member), others=lead_row.others),) + rec.companies[1:]), SPOT_THU)
    assert e.value.code == "too_few_rows"


@pytest.mark.parametrize("change, code", [
    (lambda r: replace(r, companies=(R.InsiderSpotlightCompany(lead=replace(r.companies[0].lead, amended=True),
                                                               others=r.companies[0].others),) + r.companies[1:]),
     "record_invalid"),
    # two filing days but ONE purchase line (a line belongs to one filing): the copy cannot count it
    (lambda r: replace(r, companies=(R.InsiderSpotlightCompany(lead=replace(
        r.companies[0].lead, purchases=1, amount_usd=1_000_825.0, shares=7_000.0,
        filing_dates=(date(2026, 11, 17), date(2026, 11, 18)), cost_per_share_min_usd=142.975,
        cost_per_share_max_usd=142.975), others=()),) + r.companies[1:]), "record_invalid"),
    (lambda r: isp_n(3, day=date(2026, 11, 10), start=date(2026, 11, 9), end=date(2026, 11, 10)), "stale_source"),
], ids=["amended", "lines_below_filing_days", "stale"])
def test_isp_refusals(change, code):
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(change(isp_cluster()), SPOT_THU)
    assert e.value.code == code


@pytest.mark.parametrize("low, high, printed, code", [
    (142.10, 142.10, ("$142.10", None), None),                    # one filed figure: written once
    (25.0, 25.0, ("$25.00", None), None),                          # two places at least
    (1142.10, 1143.85, ("$1,142.10", "$1,143.85"), None),
    # AS FILED, never rounded (review 2026-11-16): every place the filing carries, up to COST_MAX_PLACES
    (142.105, 142.105, ("$142.105", None), None),                  # half-up to cents would have printed $142.11
    (142.1234, 143.8501, ("$142.1234", "$143.8501"), None),
    (0.125, 0.125, ("$0.125", None), None),                        # cents would have printed $0.13, 4% off
    (0.004, 0.004, ("$0.004", None), None),                        # cents would have printed $0.00
    # two DIFFERENT filed prices that round alike are a range, never one rounded price
    (142.101, 142.104, ("$142.101", "$142.104"), None),
    # more places than a post can print as filed: that candidate is refused, never rounded
    (142.12345, 142.12345, None, "implausible_figures"),
    (142.10, 142.12345, None, "implausible_figures"),
    (0.1 + 0.2, 1.0, None, "implausible_figures"),                 # 0.30000000000000004: no filing reads so
    (-1.0, 2.0, None, "implausible_figures"), (float("nan"), 1.0, None, "implausible_figures"),
    (True, 1.0, None, "implausible_figures"), (3.0, 2.0, None, "implausible_figures"),
    (float("inf"), float("inf"), None, "implausible_figures"),
])
def test_news_v22_the_cost_pair_is_printed_as_filed_and_written_once_only_for_one_figure(low, high, printed, code):
    if code is None:
        assert T.cost_pair(low, high) == printed
    else:
        with pytest.raises(T.NewsTemplateRefused) as e:
            T.cost_pair(low, high)
        assert e.value.code == code


def test_news_v22_cost_image_never_rounds_a_filed_cost():
    """Every figure `cost_image` writes reads back as EXACTLY the filed value (an anti-rounding sweep over filed costs
    of two to four places), with two places at least and at most COST_MAX_PLACES."""
    assert T.COST_MAX_PLACES == 4
    for cents in range(1, 200_000, 13):
        for extra in ("", "3", "45"):
            value = float(f"{cents / 100:.2f}{extra}")
            text = T.cost_image(value)
            places = len(text.split(".", 1)[1])
            assert 2 <= places <= T.COST_MAX_PLACES, (value, text)
            assert float(text[1:].replace(",", "")) == value, (value, text)
            assert T.cost_pair(value, value) == (text, None)


def test_isp_a_row_prints_its_cost_as_filed_in_the_captions_and_on_its_detail_line():
    """A row's filed cost per share — its people's lowest and highest lines — is written with every place the filing
    carries, in its caption paragraph ("at $142.1234 to $143.8501 a share, as filed") and on its detail line; one price
    reads "at $25.00 a share"; a cost filed to five places DROPS that row (`drop_refused_rows`) and refuses a list it
    would leave whole (`implausible_figures`), never a rounded "as filed"."""
    rec = isp_cluster()
    lead_row = rec.companies[0]
    fine = replace(rec, companies=(R.InsiderSpotlightCompany(lead=replace(
        lead_row.lead, cost_per_share_min_usd=142.1234, cost_per_share_max_usd=143.8501), others=()),)
        + rec.companies[1:])
    out = compose(fine, SPOT_THU)
    assert ("Contoso (CTSO): the CEO disclosed buying $2 million of stock, 14,000 shares at $142.1234 to $143.8501 a "
            "share, as filed.") in out["captions"]["facebook"]
    assert out["image_spec"]["sections"][0]["rows"][0]["detail"] == "14K shares · $142.1234–$143.8501 a share, as filed"
    one = compose(isp_n(3), SPOT_THU)
    assert ("Chipotle (CMG): the CFO disclosed buying $1.2 million of stock, 24,000 shares at $50.10 a share, as "
            "filed.") in one["captions"]["facebook"]
    # the row's LOWEST filed cost (it is printed) carries five places
    five = replace(rec, companies=(R.InsiderSpotlightCompany(lead=replace(lead_row.lead,
                                                                          cost_per_share_min_usd=141.12345),
                                                             others=lead_row.others),) + rec.companies[1:])
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(five, SPOT_THU)
    assert e.value.code == "implausible_figures"


def test_isp_inside_the_members_elect_window_the_list_names_no_one(monkeypatch):
    """The REAL window (an even year's Election Day through Jan 3, judged over the run day and the two after it): a
    named record composes role-only on a day inside it (the same post as its record without names) and names each row's
    LEAD on Jan 5, 2027 — only in that row's caption paragraph, never in a first line, a drawn or narrated string."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    rec = isp_named()
    outside = compose(rec, SPOT_JAN)
    assert outside["persons"] == ["Jordan Avery"]
    for f, body in outside["captions"].items():
        first, _, rest = body.partition("\n")
        assert "Avery" not in first and "Morgan" not in first, f
    for f in ("x", "bluesky", "threads"):                   # a computed-budget caption is restricted whole
        assert "Avery" not in outside["captions"][f], f
    for k in _NO_COST_FIELDS + ("image_spec",):
        assert "Avery" not in json.dumps(outside[k]) and "Morgan" not in json.dumps(outside[k]), k
    assert ("Tailspin Toys (TLSP): the CEO, Jordan Avery, and at least one other insider disclosed buying"
            in outside["captions"]["facebook"])
    inside_day = date(2027, 1, 1)
    shifted = isp_list(*(R.InsiderSpotlightCompany(lead=replace(c.lead, filing_dates=(date(2026, 12, 31),),
                                                                earliest_trade_date=date(2026, 12, 30),
                                                                latest_trade_date=date(2026, 12, 31), purchases=3),
                                                   others=()) for c in rec.companies),
                       start=date(2026, 12, 29), end=date(2026, 12, 31))
    inside = compose(shifted, inside_day)
    assert inside["persons"] == [] and "Avery" not in json.dumps(inside)
    assert inside == compose(_without_names(shifted), inside_day)


def test_isp_the_closing_question_is_the_last_paragraph_kept_where_the_limit_allows():
    """The fixed question closes the long captions (every company's paragraph, the notes and the explainer before it) —
    the last paragraph, so a caption whose budget cuts keeps its facts and drops the question first."""
    q_isp, q_13f = T.LEXICON["isp.p.question"], T.LEXICON["f13s.p.question"]
    three = compose(isp_n(3), SPOT_THU)
    assert three["captions"]["facebook"].endswith(q_isp)
    assert compose(f13s_list2(), SPOT_THU)["captions"]["facebook"].endswith(q_13f)
    for out in (three, compose(isp_n(5), SPOT_THU)):
        assert q_isp not in out["captions"]["x"] and q_isp not in out["captions"]["tiktok"]


@pytest.mark.parametrize("field_, mutate, words", [
    ("hook", lambda o: o.__setitem__("hook", o["hook"][:-1] + ", at $142.10 a share."), True),
    ("video_script[1]", lambda o: o["video_script"].__setitem__(1, o["video_script"][1][:-1] + " as filed."), True),
    ("cards[1].body", lambda o: o["cards"][1].__setitem__("body", o["cards"][1]["body"] + " · $143.85"), False),
    ("opening_card.headline", lambda o: o["opening_card"].__setitem__("headline", "Contoso's CEO at $142.10"), False),
    # the cover's rows: compact, never a detail line (owner 2026-11-20 "All rows, compact")
    ("opening_card.list.rows[0].cells[1]", lambda o: o["opening_card"]["list"]["rows"][0]["cells"].__setitem__(
        1, "CEO · $142.10 a share"), True),
    ("image_post.paragraphs[0]", lambda o: o["image_post"]["paragraphs"].__setitem__(
        0, o["image_post"]["paragraphs"][0] + " Cost a share, as filed: $142.10."), True),
    ("captions.youtube_title", lambda o: o["captions"].__setitem__("youtube_title", "Contoso at $141.38"), False),
    # the picture beyond its rows' detail lines
    ("image_spec.title", lambda o: o["image_spec"].__setitem__("title", o["image_spec"]["title"]
                                                               + " at $142.10 a share"), True),
    ("image_spec.sections[0].rows[0].cells[2]", lambda o: o["image_spec"]["sections"][0]["rows"][0][
        "cells"].__setitem__(2, "$142.10"), False),
    ("image_spec.kicker", lambda o: o["image_spec"].__setitem__("kicker", "AS FILED · FORM 4"), True),
    ("image_spec.sections[0].heading", lambda o: o["image_spec"]["sections"][0].__setitem__(
        "heading", "Total purchases at $142.05"), False),
], ids=["hook", "narration", "card", "cover", "cover_row", "alt_text", "youtube_title", "picture_title",
        "picture_total", "picture_kicker", "picture_heading"])
def test_isp_the_cost_placement_check_refuses_a_cost_in_the_video_or_the_alt_text(field_, mutate, words):
    """Defence in depth (`_validate_cost_placement`): a stored insider list whose cost per share — a figure or its words
    — reached the video, the cover (its compact rows included), the alt text, the YouTube title or the picture outside
    its rows' detail lines is a `placement` violation: with the record, every person's figure (the others' $141.38
    included) and the words; without it, the words. The untouched output passes, though its captions and detail lines
    DO carry the cost (anti-vacuity)."""
    rec = isp_cluster()
    out = compose(rec, SPOT_THU)
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []
    # the lead row's detail line carries its people's lowest and highest filed costs ($141.38, the director's, to
    # $143.85, the CEO's)
    assert "a share, as filed" in out["captions"]["facebook"] and "$141.38" in json.dumps(out["image_spec"])
    bad = copy.deepcopy(out)
    mutate(bad)
    hits = {x["field"] for x in T.validate_package(bad, record=rec) if x["code"] == "placement"}
    assert field_ in hits, hits
    blind = {x["field"] for x in T.validate_package(bad) if x["code"] == "placement"}
    assert (field_ in blind) is words, blind
    # the words' pattern reads words, not "was filed"; only the PICTURE's rows may carry a detail line
    assert T._COST_PHRASE_RE.search("Cost a share, as filed") and not T._COST_PHRASE_RE.search("was filed on Nov 18")
    assert T._DETAIL_FIELD_RE.fullmatch("image_spec.sections[0].rows[4].detail")
    assert not T._DETAIL_FIELD_RE.fullmatch("opening_card.list.rows[0].detail")


def test_isp_a_detail_line_on_the_cover_is_refused():
    """Owner decision 5 (2026-11-15) and the compact cover (2026-11-20): a cover row carrying a detail line — the filed
    cost per share on a video surface — is a `placement` violation, with or without the record."""
    rec = isp_cluster()
    out = compose(rec, SPOT_THU)
    bad = copy.deepcopy(out)
    bad["opening_card"]["list"]["rows"][0]["detail"] = out["image_spec"]["sections"][0]["rows"][0]["detail"]
    for record in (rec, None):
        assert "opening_card.list.rows[0].detail" in {
            x["field"] for x in T.validate_package(bad, record=record) if x["code"] == "placement"}


def test_isp_the_cost_placement_check_also_reads_a_cost_rounded_to_cents():
    """A filed $142.1234 that a builder wrote ROUNDED ("$142.12") into the video or the picture's title is still the
    filing's cost: the check matches each figure as filed AND rounded to cents. The untouched output passes."""
    rec = isp_cluster()
    lead_row = rec.companies[0]
    rec = replace(rec, companies=(R.InsiderSpotlightCompany(lead=replace(
        lead_row.lead, cost_per_share_min_usd=142.1234, cost_per_share_max_usd=143.8501), others=lead_row.others),)
        + rec.companies[1:])
    out = compose(rec, SPOT_THU)
    assert T.validate_package(out, record=rec) == []
    for field_, mutate in (("cards[1].body", lambda o: o["cards"][1].__setitem__("body", o["cards"][1]["body"]
                                                                                 + " · $142.12")),
                           ("image_spec.title", lambda o: o["image_spec"].__setitem__(
                               "title", o["image_spec"]["title"] + " · $143.85"))):
        bad = copy.deepcopy(out)
        mutate(bad)
        assert field_ in {x["field"] for x in T.validate_package(bad, record=rec) if x["code"] == "placement"}


def test_isp_composition_is_deterministic():
    """The same record composes byte for byte the same output (compose and revalidate re-derive it at create_posts)."""
    for factory, day in ((lambda: isp_n(5), SPOT_THU), (isp_cluster, SPOT_THU), (isp_named, SPOT_JAN),
                         (ceo_roundup5, MON)):
        a, b = compose(factory(), day), compose(factory(), day)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── news-v22: the first-line prefixes (B decision 2, owner 2026-11-15 "Loosen caption hype words") ──────────────────
#
# Scope "Spotlights + Form 4 + earnings": a closed, code-owned prefix opens a caption's FIRST line — the insider
# spotlight "🚨 BREAKING:" while fresh, else "🚨 Insider purchase alert:"; the weekly Form 4 roundups the alert only, never
# BREAKING; earnings "🚨 BREAKING:" while fresh, else none; the 13F spotlight "📊 13F filing:". Never the YouTube title,
# the picture, the alt text or the video. Behind MARKETING_FIRST_LINE_PREFIX (off: every caption byte for byte today's),
# frozen per build into `output.first_line_prefix_on`. The validator's exemption is POSITIONAL, with must-reject twins.

BREAKING = "\U0001F6A8 BREAKING:"
ALERT = "\U0001F6A8 Insider purchase alert:"
F13_FILING = "\U0001F4CA 13F filing:"
_PREFIXES = frozenset({BREAKING, ALERT, F13_FILING})
#: the samples whose series may carry a prefix (the rest must compose byte for byte as with the switch off)
_PREFIX_SAMPLES = [s for s in SAMPLES if s[1]().series in T.PREFIX_IDS_BY_SERIES]


def compose_on(rec, run_date, store_state="live", allow_x_url=False):
    return T.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url, first_line_prefix=True)


def test_news_v22_the_first_line_prefixes_are_a_closed_pinned_table():
    assert T.FIRST_LINE_PREFIXES == {"insider.breaking": BREAKING, "insider.alert": ALERT,
                                     "thirteen_f.filing": F13_FILING, "earnings.breaking": BREAKING}
    assert T.PREFIX_IDS_BY_SERIES == {
        "insider_spotlight": ("insider.breaking", "insider.alert"), "ceo_buys": ("insider.alert",),
        "insider_buys": ("insider.alert",), "earnings": ("earnings.breaking",),
        "thirteen_f_spotlight": ("thirteen_f.filing",)}
    assert T.BREAKING_PREFIX_IDS == {"insider.breaking", "earnings.breaking"}
    # ONE freshness constant (company_news_rules'), never a second definition
    assert T.BREAKING_MAX_BUSINESS_DAYS is R.BREAKING_MAX_BUSINESS_DAYS and T.BREAKING_MAX_BUSINESS_DAYS == 1
    assert set(T.PREFIX_IDS_BY_SERIES) <= set(T.SERIES_SPECS)
    used = {i for ids in T.PREFIX_IDS_BY_SERIES.values() for i in ids}
    assert used == set(T.FIRST_LINE_PREFIXES) and T.BREAKING_PREFIX_IDS <= used
    # a weekly roundup and a 13F never say BREAKING; the series outside the owner's scope carry nothing
    for series in ("ceo_buys", "insider_buys", "thirteen_f_spotlight"):
        assert not set(T.PREFIX_IDS_BY_SERIES[series]) & T.BREAKING_PREFIX_IDS, series
    assert not {"thirteen_f", "money_map", "company_stakes", "congress_count", "theme_explainer"} & set(
        T.PREFIX_IDS_BY_SERIES)
    # the only strings a prefix may open: every caption body but the YouTube title, and every composed caption
    assert T._PREFIX_FIELDS == ({f"captions.{f}" for f in post_copy.CAPTION_FIELDS if f != "youtube_title"}
                                | {f"posts.{p}.caption" for p in post_copy.PLATFORMS})
    assert not {"captions.youtube_title", "posts.youtube.title", "hook", "image_post.title"} & T._PREFIX_FIELDS


@pytest.mark.parametrize("pid, news_hits, x_weight, bluesky_len", [
    ("insider.breaking", {"breaking"}, 13, 12), ("insider.alert", {"alert"}, 27, 26),
    ("thirteen_f.filing", set(), 15, 14), ("earnings.breaking", {"breaking"}, 13, 12)])
def test_news_v22_each_prefix_has_one_emoji_first_and_only_its_own_hype_word(pid, news_hits, x_weight, bluesky_len):
    """One single-code-point emoji at index 0 (no VS16: `clean(p) == p`), a space, a label ending in ":"; nothing the
    banned / forecast / verb tables read, and of the news ban only its own word ("breaking" / "alert") — the
    POSITIONAL exemption (`_validate_words`) is the one place it may stand. X weighs it 13 / 27 / 15 with its space
    (the emoji is 2), Bluesky's code points 12 / 26 / 14 (design_content step 7)."""
    from app.services.marketing.compliance import _is_emoji

    p = T.FIRST_LINE_PREFIXES[pid]
    assert [c for c in p if _is_emoji(c)] == [p[0]] and ord(p[0]) > 0xFFFF and p[1] == " "
    assert clean(p) == p and "️" not in p and p.endswith(":") and p == p.strip()
    folded = fold(p)
    assert not copy_rules.BANNED_COPY.search(folded) and not copy_rules.FORECAST_COPY.search(folded)
    assert not T.OFF_TABLE_VERBS_RE.search(folded) and not T.EARNINGS_BANNED_RE.search(folded)
    assert set(T.NEWS_BANNED_RE.findall(folded)) == news_hits
    assert not set("!#$%@") & set(p) and not R.congress_name_hits(p)
    assert post_copy.x_weighted_length(p + " ") == x_weight
    assert post_copy.measured_length("bluesky", p + " ") == bluesky_len
    assert len((p + " ").encode("utf-8")) == bluesky_len + 3         # Threads' byte cap: the emoji is 4 bytes


def _fresh_isp(filed, run, *, traded=None):
    """A one-person insider spotlight whose lead filed ONE Form 4 on `filed` (traded on `traded`, the filing day by
    default), read on `run` — the window the week before the run."""
    traded = traded or (filed, filed)
    lead = ip("CTSO", "Contoso", "ceo", None, 2_001_650.0, 14_000.0, 2, [filed], list(traded), (142.10, 143.85))
    return spotlight(lead, start=run - timedelta(days=7), end=run - timedelta(days=1))


def isp_fresh():
    """An insider list EVERY filing of which is fresh (Wed Nov 18, read Thu Nov 19): BREAKING — owner decisions
    2026-11-20, a list is BREAKING only when every person's filing is (`isp_cluster`'s director filed Nov 17: the alert)."""
    return _fresh_isp(date(2026, 11, 18), SPOT_THU)


@pytest.mark.parametrize("filed, run, prefix", [
    (date(2026, 11, 16), date(2026, 11, 17), BREAKING),      # Tue run, Mon filing
    (date(2026, 11, 17), date(2026, 11, 19), ALERT),         # Thu run, Tue filing: two business days
    (date(2026, 11, 20), date(2026, 11, 21), BREAKING),      # Sat run, Fri filing
    (date(2026, 11, 19), date(2026, 11, 21), BREAKING),      # Sat run, Thu filing: Friday is the one business day
    (date(2026, 11, 18), date(2026, 11, 21), ALERT),         # Sat run, Wed filing
    (date(2026, 11, 13), date(2026, 11, 17), ALERT),         # Tue run, Fri filing: Mon and Tue
    (date(2026, 11, 25), date(2026, 11, 27), BREAKING),      # Fri run, Wed filing: Thanksgiving is no business day
    (date(2026, 11, 24), date(2026, 11, 27), ALERT),         # Fri run, Tue filing: Wed and Fri
    (date(2026, 12, 24), date(2026, 12, 26), BREAKING),      # Sat run, Thu filing: Christmas Day is no business day
], ids=["tue_mon", "thu_tue", "sat_fri", "sat_thu", "sat_wed", "tue_fri", "thanksgiving", "thanksgiving_tue",
        "christmas"])
def test_news_v22_breaking_is_at_most_one_federal_business_day_after_the_leads_filing(filed, run, prefix):
    rec = _fresh_isp(filed, run)
    assert T._breaking_fresh(rec, run) is (prefix == BREAKING)
    out = compose_on(rec, run)
    assert out["first_line_prefix"] == prefix and out["first_line_prefix_on"] is True
    assert out["captions"]["facebook"].startswith(f"{prefix} ")
    assert T.validate_package(out, record=rec) == []
    # the switch off: no prefix, whatever the freshness
    assert compose(rec, run)["first_line_prefix"] is None


def test_news_v22_a_late_lead_is_an_alert_never_breaking():
    """`isp_single`: filed Mon Nov 16, read Tue Nov 17 — one business day, but its trades (Nov 5-6) are LATE (more than
    two business days before the filing): its copy states the trade dates, and it is never BREAKING."""
    rec = isp_single()
    assert T._late_trades(rec.lead) and T._business_days_after(rec.lead.filing_dates[-1], SPOT_TUE, stop_above=1) == 1
    assert not T._breaking_fresh(rec, SPOT_TUE)
    assert compose_on(rec, SPOT_TUE)["first_line_prefix"] == ALERT
    # the same filing, timely: BREAKING
    timely = _fresh_isp(date(2026, 11, 16), SPOT_TUE, traded=(date(2026, 11, 13), date(2026, 11, 16)))
    assert compose_on(timely, SPOT_TUE)["first_line_prefix"] == BREAKING


@pytest.mark.parametrize("report, run, prefix", [
    (date(2026, 11, 18), date(2026, 11, 19), BREAKING),      # Thu run, Wed report
    (date(2026, 11, 17), date(2026, 11, 19), None),          # Thu run, Tue report: stale, no prefix at all
    (date(2026, 11, 5), date(2026, 11, 12), None),           # the `earnings` sample
    # C0 (2026-11-16): earnings age on the EXCHANGE's calendar. Veterans Day (Wed Nov 11, 2026) is no federal business
    # day, but the exchanges trade — so a Tuesday report read on Thursday is two sessions old: NOT BREAKING (the federal
    # count said one; the `earnings_profit` sample)
    (date(2026, 11, 10), date(2026, 11, 12), None),
    # a day both calendars close (Thanksgiving, Thu Nov 26) never ages it: Wed report, Fri run → one day, BREAKING
    (date(2026, 11, 25), date(2026, 11, 27), BREAKING),
    # Good Friday (Apr 3, 2026) closes the exchange, not the federal government: it still ages the report (never
    # looser than the federal count) — Thu report, Mon run → two days, no prefix
    (date(2026, 4, 2), date(2026, 4, 6), None),
    # a weekend never ages it: Fri report, Mon run → BREAKING
    (date(2026, 11, 13), date(2026, 11, 16), BREAKING),
], ids=["wed_thu", "tue_thu", "sample_stale", "veterans_day", "thanksgiving", "good_friday", "weekend"])
def test_news_v22_earnings_is_breaking_only_while_fresh_else_no_prefix(report, run, prefix):
    rec = earnings(report_date=report, **({"period_end": date(2026, 2, 28)} if report.month < 6 else {}))
    out = compose_on(rec, run)
    assert out["first_line_prefix"] == prefix
    assert T.validate_package(out, record=rec) == []
    if prefix is None:
        assert out["captions"] == compose(rec, run)["captions"]       # an earnings post carries no other prefix


def test_c0_the_earnings_count_is_never_lower_than_the_federal_count():
    """The switch to the exchange's calendar can only REMOVE a BREAKING label: over three years of (report, run) pairs
    up to a week apart, the earnings count is never below the federal business-day count — and it is above it exactly
    when a federal holiday the exchanges trade through (Columbus Day, Veterans Day) lies in between."""
    d = date(2025, 12, 1)
    while d < date(2028, 12, 31):
        for gap in range(1, 8):
            run = d + timedelta(days=gap)
            earn = T._earnings_days_after(d, run, stop_above=10)
            fed = T._business_days_after(d, run, stop_above=10)
            assert earn >= fed, (d, run, earn, fed)
        d += timedelta(days=1)
    assert T._earnings_days_after(date(2026, 11, 10), date(2026, 11, 12), stop_above=5) == 2
    assert T._business_days_after(date(2026, 11, 10), date(2026, 11, 12), stop_above=5) == 1
    assert T._earnings_days_after(date(2026, 11, 25), date(2026, 11, 27), stop_above=5) == 1
    # the table's own coverage ends: every weekday counts beyond it (stricter, never looser)
    assert T._earnings_days_after(date(2031, 12, 24), date(2031, 12, 26), stop_above=5) == 2


@pytest.mark.parametrize("series, role", [("ceo_buys", "ceo"), ("insider_buys", "cfo")])
def test_news_v22_a_weekly_roundup_is_an_insider_alert_never_breaking(series, role):
    """A Form 4 ROUNDUP is a week's purchases: the alert prefix only, even when its lead filed the business day before
    the run (Fri Nov 13, read Mon Nov 16) — never BREAKING (owner scope: the weekly roundup is never breaking news)."""
    rec = week(buy("GME", "GameStop", role, None, 74_400_000.0, 3_000_000.0, 2, [date(2026, 11, 13)]),
               buy("LOW", "Lowe's", role, None, 2_300_000.0, 9_812.0, 1, [date(2026, 11, 12)]), series=series)
    assert T._breaking_fresh(rec, MON) is False
    out = compose_on(rec, MON)
    assert out["first_line_prefix"] == ALERT
    assert out["captions"]["facebook"].startswith(f"{ALERT} At least 3 ")        # owner 2026-11-20: never two
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_news_v22_the_switch_changes_captions_only_and_off_is_byte_for_byte_today(name, factory, run_date):
    """Off (the default): the composition IS today's (the goldens pin it), the prefix None. On: only the caption
    bodies and their composed posts may change — never the video, the picture, the alt text, the YouTube title, the
    persons, the logos, the dropped outlets (content first: a prefix never costs an outlet) or a post's code-owned
    suffix; a series without a prefix composes byte for byte as off, but for its frozen switch."""
    off = compose(factory(), run_date)
    assert off == T.compose(factory(), run_date=run_date, store_state="live", allow_x_url=False)
    assert off["first_line_prefix_on"] is False and off["first_line_prefix"] is None
    on = compose_on(factory(), run_date)
    moved = {"captions", "posts", "first_line_prefix", "first_line_prefix_on"}
    assert {k: v for k, v in on.items() if k not in moved} == {k: v for k, v in off.items() if k not in moved}
    assert sorted(on["posts"]) == sorted(off["posts"]) and sorted(on["captions"]) == sorted(off["captions"])
    assert on["captions"]["youtube_title"] == off["captions"]["youtube_title"]
    assert on["posts"]["youtube"]["title"] == off["posts"]["youtube"]["title"]
    for platform, post in on["posts"].items():
        field_ = "youtube_description" if platform == "youtube" else platform
        assert post["caption"][len(on["captions"][field_]):] == off["posts"][platform]["caption"][
            len(off["captions"][field_]):], (name, platform)
    pfx = on["first_line_prefix"]
    if factory().series not in T.PREFIX_IDS_BY_SERIES:
        assert pfx is None and on["captions"] == off["captions"] and on["posts"] == off["posts"]
    for f, body in on["captions"].items():
        starts = [p for p in _PREFIXES if body.startswith(p)]
        assert starts in ([], [pfx]), (name, f)
        if starts:
            assert body.startswith(f"{pfx} ") and f != "youtube_title" and body.count(pfx) == 1


@pytest.mark.parametrize("name, factory, run_date", _PREFIX_SAMPLES, ids=[s[0] for s in _PREFIX_SAMPLES])
def test_news_v22_the_prefix_is_tried_content_first_on_the_headline_only_outlets(name, factory, run_date):
    """X, Bluesky and Threads carry the headline alone: per headline, long → short → min, the prefixed form (with its
    `after_prefix` twin) and then the plain one — the first that fits. The prefix is the first thing given up, never a
    longer headline. The 13F spotlight LIST (owner feedback 2026-11-19) adds its rows, content first: every headline
    with all its rows before any with one row fewer — and on Threads never a body whose picture caption or extra
    video caption would pass its cap."""
    rec = factory()
    on = compose_on(rec, run_date)
    pfx = on["first_line_prefix"]
    if pfx is None:                 # a stale earnings report: no prefix applies, no caption carries one
        assert rec.series == "earnings" and not [b for b in on["captions"].values() if b.startswith(tuple(_PREFIXES))]
        return
    parts = _parts(rec, run_date)

    def order_of(f):
        # news-v23: the 13F list's OWN paragraphs per field (`_Parts.field_paragraphs`) and the fewest it must keep
        # (`_Parts.field_required`: the rows its "+N more changes in the caption" promises)
        rows = parts.field_paragraphs.get(f, [])
        need = parts.field_required.get(f, 0)
        return [("\n\n".join([c, *rows[:len(rows) - cut]]), c.startswith(pfx), len(rows) - cut)
                for cut in range(len(rows) - need + 1) for h in parts.image_headlines
                for c in (f"{pfx} {parts.after_prefix.get(h, h)}", h)]

    def threads_caption(body):
        return post_copy.compose("threads", {"threads": body}, category=f"news:{rec.series}", run_date=run_date,
                                 store_state="live", authorship="template").caption

    def video_too_long(body):
        try:
            post_copy.extra_video_caption("threads", threads_caption(body), run_date, "template")
        except ValueError:
            return True
        return False

    for f in ("x", "bluesky", "threads"):
        budget = post_copy.body_budget(f, f"news:{rec.series}", run_date, allow_x_url=False, store_state="live",
                                       authorship="template")
        # Threads: a prefixed caption (or one with rows) over the picture's byte cap gives way to the plain headline
        fitting = [c for c, prefixed, kept in order_of(f) if post_copy.measured_length(f, c) <= budget and not (
            f == "threads" and (prefixed or kept) and (
                len(threads_caption(c).encode("utf-8")) > T.THREADS_PICTURE_CAPTION_MAX_BYTES
                or (kept and video_too_long(c))))]
        if not fitting:
            if parts.text_paragraphs and f in T.TEXT_PLAN_OUTLETS:
                # no body keeps the picture's promise: posted as TEXT (frozen as text), never dropped — owner decisions
                # 2026-11-20, a Form 4 list's "+N more companies in the caption" (or its moved note) on X / Bluesky
                assert f in on["text_outlets"], (name, f)
                continue
            assert f not in on["captions"], (name, f)
            continue
        assert on["captions"][f] == fitting[0], (name, f)


def test_news_v22_the_13f_after_prefix_twins_say_13f_once():
    """"📊 13F filing:" says "13F": the 13F spotlight's prefixed headline is its after-prefix twin (`f13s.list.hp.*`) —
    the filer the subject of the hook's verbs, without "latest 13F" (the prefix says it) — never "13F" twice in one
    line; every other series writes its headline as it is after a prefix."""
    out = compose_on(f13s_list2(), SPOT_SAT)
    fb = out["captions"]["facebook"]
    assert fb.split("\n\n")[0] == f"{F13_FILING} Fabrikam Capital reduced 1 holding and increased 1."
    assert fb.count("13F") == 3 and "latest 13F" not in fb.split("\n\n")[0]   # prefix, explainer, closing question
    for _n, factory, run_date in _F13S_SAMPLES:
        parts = _parts(factory(), run_date)
        assert set(parts.after_prefix) == set(parts.image_headlines)
        assert all("13F" not in t for t in parts.after_prefix.values())
    for _n, factory, run_date in _ISP_SAMPLES:
        assert _parts(factory(), run_date).after_prefix == {}


#: The prefixed compositions, pinned verbatim (switch on; store live; no X URL) — beside the unprefixed goldens above,
#: which the switch off still composes byte for byte. Regenerate only with a TEMPLATE_VERSION bump.
GOLDEN_PREFIXED = {
    # owner decisions 2026-11-20 (the insider LIST): a list is BREAKING only when every person's filing is fresh —
    # isp_cluster's director filed Nov 17 (two business days before Thu Nov 19) and isp_single's lead traded late:
    # both the alert. Content first: Bluesky's budget takes the plain headline.
    "isp_cluster": {
        "first_line_prefix": ALERT,
        "x": f"{ALERT} Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18.",
        "bluesky": "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18.",
        "facebook": (
            f"{ALERT} Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 17-18. "
            "The largest total: Contoso (CTSO), $2.7 million.\n\nContoso (CTSO): the CEO and at least 2 other insiders "
            "disclosed buying $2.7 million of stock, about 19,000 shares at $141.38 to $143.85 a share, as filed.\n\n"
            "Litware (LTW): a director disclosed buying $140,000 of stock, 3,500 shares at $40.00 a share, as filed."
            "\n\nProseware (PRSW): the CFO disclosed buying $120,000 of stock, 4,800 shares at $25.00 a share, as "
            "filed.\n\nA Form 4 is the report a company's officers and directors file within two business days of a "
            "trade. Amounts are each filing's share count times the cost it reports for each share.\n\nWhich detail of "
            "a Form 4 do you read first: who filed, the trade dates or the amount?"),
    },
    "isp_single": {
        "first_line_prefix": ALERT,
        "x": f"{ALERT} Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16.",
        # Bluesky: the alert does not fit beside the headline — content first, the plain headline
        "bluesky": "Insiders at 3 companies disclosed buying their own company's stock in Form 4s filed Nov 16.",
    },
    # the LIST (owner feedback 2026-11-19): every change counted — the filer the subject of the hook's verbs; or "N of
    # {filer}'s holding changes"; Threads adds as many whole rows as fit, content first (the min headline + two rows)
    "f13s_list2": {
        "first_line_prefix": F13_FILING,
        "x": f"{F13_FILING} Fabrikam Capital reduced 1 holding and increased 1.",
        "threads": (f"{F13_FILING} Fabrikam Capital reduced 1 holding and increased 1.\n\nNorthwind (NWT): reduced to "
                    "41.9 million shares, from 60 million the quarter before.\n\nLitware (LTW): increased to 12.5 "
                    "million shares, from 8 million."),
    },
    "f13s_list5": {
        "first_line_prefix": F13_FILING,
        # news-v23: "closed out"; share counts only; Threads carries the rows its extra video's cover leaves out (the
        # fourth and fifth, "+2 more changes in the caption" — review 2026-11-20: three rows on the cover) — the picture
        # draws all five — so the counts headline fits too
        "x": (f"{F13_FILING} 5 of Fabrikam Capital's holding changes: 2 reduced, 1 new, 1 increased, 1 closed "
              "out."),
        "threads": (f"{F13_FILING} 5 of Fabrikam Capital's holding changes: 2 reduced, 1 new, 1 increased, 1 closed "
                    "out.\n\nWoodgrove Bank (WGB): closed out, from 4.3 million shares the quarter before.\n\n"
                    "Contoso (CTSO): reduced to 2 million shares, from 3 million."),
    },
    "ceo_roundup": {
        "first_line_prefix": ALERT,
        "x": f"{ALERT} At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
        "bluesky": "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
    },
    # C0 (2026-11-16): reported Tue Nov 10, read Thu Nov 12 — Veterans Day is a trading day, so the report is two
    # sessions old on the exchange's calendar: no prefix (the same record read Wednesday is BREAKING, below)
    # news-v23 (owner 2026-11-19): ONE post of the day's companies — the first line names them
    "earnings_profit": {
        "first_line_prefix": None,
        "x": ("Fabrikam and Contoso reported results against analyst estimates; an estimate's basis can differ from "
              "the official accounts."),
        "threads": ("Fabrikam and Contoso reported results against analyst estimates; an estimate's basis can differ "
                    "from the official accounts."),
    },
}


def test_news_v22_an_earnings_report_read_the_next_session_is_breaking_on_threads_and_fits_x_plain():
    """The prefixed earnings golden (moved off the sample's Thursday by C0): `earnings_profit` read Wed Nov 11, one
    session after its Tuesday report — Threads carries the BREAKING prefix; X keeps the plain headline (content first)."""
    out = compose_on(earnings_profit(), date(2026, 11, 11))
    assert out["first_line_prefix"] == BREAKING
    assert out["captions"]["threads"] == (f"{BREAKING} Fabrikam and Contoso reported results against analyst estimates; "
                                          "an estimate's basis can differ from the official accounts.")
    assert out["captions"]["x"] == ("Fabrikam and Contoso reported results against analyst estimates; an estimate's "
                                    "basis can differ from the official accounts.")
    assert T.validate_package(out, record=earnings_profit()) == []


@pytest.mark.parametrize("name", sorted(GOLDEN_PREFIXED))
def test_news_v22_the_prefixed_goldens(name):
    factory, run_date = next((f, d) for n, f, d in SAMPLES if n == name)
    out = compose_on(factory(), run_date)
    for key, value in GOLDEN_PREFIXED[name].items():
        got = out[key] if key == "first_line_prefix" else out["captions"][key]
        assert got == value, f"{name}.{key}"
    # the unprefixed goldens still hold for the same samples with the switch off
    golden = {**GOLDEN, **GOLDEN_2B, **GOLDEN_SPOTLIGHT}[name]
    off = compose(factory(), run_date)
    for key, value in golden.items():
        assert (off["captions"][key] if key in _GOLDEN_CAPTION_KEYS else off[key]) == value, f"{name}.{key}"


def test_news_v22_compose_refuses_a_switch_that_is_not_a_bool():
    for junk in (1, 0, "true", "", None):
        with pytest.raises(TypeError, match="first_line_prefix"):
            T.compose(isp_cluster(), run_date=SPOT_THU, store_state="live", allow_x_url=False,
                      first_line_prefix=junk)


# ── the must-reject twins: the prefix is exempt ONLY at position 0 of a caption body / composed caption ──────────────


def _with_body(o, field_, new):
    """Set caption body `field_` to `new` and keep its composed post consistent (the post caption starts with it)."""
    old = o["captions"][field_]
    o["captions"][field_] = new
    if field_ == "youtube_title":
        o["posts"]["youtube"]["title"] = new
        return
    platform = "youtube" if field_ == "youtube_description" else field_
    cap = o["posts"][platform]["caption"]
    assert cap.startswith(old)
    o["posts"][platform]["caption"] = new + cap[len(old):]


def _swap_prefix(o, old, new):
    """Every caption (and composed post) opening on `old` opens on `new`, and the declared prefix is `new`."""
    for f, body in list(o["captions"].items()):
        if body.startswith(f"{old} "):
            _with_body(o, f, f"{new} " + body[len(old) + 1:])
    o["first_line_prefix"] = new


def _para2(o, field_, lead):
    head, second, *rest = o["captions"][field_].split("\n\n")
    _with_body(o, field_, "\n\n".join([head, f"{lead}{second}", *rest]))


def _drawn_first(o, path, text):
    """Prefix the string at `path` (a dotted path into the output) with `text`."""
    obj = o
    *keys, last = path
    for k in keys:
        obj = obj[k]
    obj[last] = f"{text}{obj[last]}"


_TWINS = [
    # (id, mutate, the (field, code) pairs that must be refused)
    ("paragraph_two", lambda o: _para2(o, "facebook", f"{BREAKING} "),
     {("captions.facebook", "structure"), ("captions.facebook", "banned_word"), ("posts.facebook.caption", "structure")}),
    ("youtube_title", lambda o: _with_body(o, "youtube_title", f"{BREAKING} {o['captions']['youtube_title']}"),
     {("captions.youtube_title", "structure"), ("posts.youtube.title", "structure")}),
    ("hook", lambda o: _drawn_first(o, ("hook",), f"{BREAKING} "), {("hook", "structure")}),
    ("narration", lambda o: _drawn_first(o, ("video_script", 0), f"{BREAKING} "), {("video_script[0]", "structure")}),
    ("card_title", lambda o: _drawn_first(o, ("cards", 0, "title"), f"{BREAKING} "), {("cards[0].title", "structure")}),
    ("card_body", lambda o: _drawn_first(o, ("cards", 1, "body"), f"{ALERT} "), {("cards[1].body", "structure")}),
    ("opening_card", lambda o: _drawn_first(o, ("opening_card", "headline"), f"{BREAKING} "),
     {("opening_card.headline", "structure")}),
    ("image_spec", lambda o: _drawn_first(o, ("image_spec", "title"), f"{BREAKING} "),
     {("image_spec.title", "structure")}),
    ("alt_title", lambda o: _drawn_first(o, ("image_post", "title"), f"{BREAKING} "),
     {("image_post.title", "structure")}),
    ("alt_paragraph", lambda o: _drawn_first(o, ("image_post", "paragraphs", 0), f"{BREAKING} "),
     {("image_post.paragraphs[0]", "structure")}),
    ("breaking_word_in_paragraph_two", lambda o: _para2(o, "facebook", "BREAKING: "),
     {("captions.facebook", "banned_word"), ("posts.facebook.caption", "banned_word")}),
    ("alert_word_in_paragraph_two", lambda o: _para2(o, "facebook", "Insider purchase alert: "),
     {("captions.facebook", "banned_word")}),
    ("breaking_word_without_its_emoji", lambda o: _with_body(o, "x", "BREAKING:" + o["captions"]["x"][len(BREAKING):]),
     {("captions.x", "banned_word"), ("posts.x.caption", "banned_word")}),
    ("a_second_emoji", lambda o: _with_body(o, "x", f"{BREAKING} \U0001F6A8 " + o["captions"]["x"][len(BREAKING) + 1:]),
     {("captions.x", "structure"), ("posts.x.caption", "structure")}),
    ("no_space_after_it", lambda o: _with_body(o, "x", BREAKING + o["captions"]["x"][len(BREAKING) + 1:]),
     {("captions.x", "structure"), ("posts.x.caption", "structure")}),
    ("not_in_the_table", lambda o: _swap_prefix(o, BREAKING, "\U0001F525 BREAKING:"),
     {("first_line_prefix", "structure"), ("captions.x", "structure"), ("captions.x", "banned_word")}),
    ("switch_off_prefix_declared", lambda o: o.update(first_line_prefix_on=False),
     {("first_line_prefix", "structure"), ("captions.x", "structure"), ("captions.x", "banned_word")}),
    ("switch_off_nothing_declared", lambda o: o.update(first_line_prefix_on=False, first_line_prefix=None),
     {("captions.x", "structure"), ("captions.x", "banned_word")}),
    ("not_a_string", lambda o: o.update(first_line_prefix=1), {("first_line_prefix", "schema")}),
    ("missing", lambda o: o.pop("first_line_prefix"), {("first_line_prefix", "schema")}),
]


@pytest.mark.parametrize("mutate, refused", [t[1:] for t in _TWINS], ids=[t[0] for t in _TWINS])
def test_news_v22_the_prefix_anywhere_but_position_zero_of_a_caption_is_refused(mutate, refused):
    """Must-reject twins (marketing.md §7: a positional exemption needs them): the honest twin — the BREAKING prefix at
    position 0 of every caption it fits, the YouTube description's included — passes with the record (without it,
    only `first_line_prefix` is refused: a BREAKING prefix cannot show its freshness); each mutation is refused where
    it lands, the record notwithstanding."""
    rec = isp_fresh()
    honest = compose_on(rec, SPOT_THU)
    assert honest["first_line_prefix"] == BREAKING
    assert all(honest["captions"][f].startswith(f"{BREAKING} ") for f in ("x", "bluesky", "facebook", "tiktok",
                                                                        "youtube_description", "instagram"))
    assert honest["posts"]["youtube"]["caption"].startswith(f"{BREAKING} ")
    assert T.validate_package(honest, record=rec) == []
    bad = copy.deepcopy(honest)
    mutate(bad)
    assert bad != honest
    got = {(x["field"], x["code"]) for x in T.validate_package(bad, record=rec)}
    assert refused <= got, sorted(got)


def _twin_on(factory, run_date, old, new):
    rec = factory()
    honest = compose_on(rec, run_date)
    assert honest["first_line_prefix"] == old and T.validate_package(honest, record=rec) == []
    bad = copy.deepcopy(honest)
    _swap_prefix(bad, old, new)
    return rec, honest, bad


def test_news_v22_breaking_on_a_weekly_roundup_is_refused():
    rec, honest, bad = _twin_on(ceo_roundup, MON, ALERT, BREAKING)
    got = {(x["field"], x["code"]) for x in T.validate_package(bad, record=rec)}
    assert {("first_line_prefix", "structure"), ("captions.facebook", "structure"),
            ("captions.facebook", "banned_word")} <= got
    assert "field_mismatch" in {x["code"] for x in T.revalidate(bad, fact_sheet=sheet(rec), run_date=MON)}


def test_news_v22_the_insider_prefix_on_a_13f_output_is_refused():
    rec, honest, bad = _twin_on(f13s_list2, SPOT_SAT, F13_FILING, ALERT)
    got = {(x["field"], x["code"]) for x in T.validate_package(bad, record=rec)}
    assert {("first_line_prefix", "structure"), ("captions.x", "structure"), ("captions.x", "banned_word")} <= got
    # ... and the 13F's own prefix on an insider spotlight
    rec2, _h, bad2 = _twin_on(isp_fresh, SPOT_THU, BREAKING, F13_FILING)
    assert ("first_line_prefix", "structure") in {(x["field"], x["code"]) for x in T.validate_package(bad2, record=rec2)}


def test_news_v22_breaking_on_a_stale_record_is_refused_by_validate_and_revalidate():
    """The lead filed Tue Nov 17, read Thu Nov 19 — two business days: the alert. Swapped to BREAKING, the package is
    refused with the record (stale) and without it (unprovable), and revalidate refuses it twice over: the composition
    differs (`field_mismatch`) and the rule fails (`structure`)."""
    stale = lambda: _fresh_isp(date(2026, 11, 17), SPOT_THU)      # noqa: E731
    rec, honest, bad = _twin_on(stale, SPOT_THU, ALERT, BREAKING)
    for record in (rec, None):
        got = [x for x in T.validate_package(bad, record=record) if x["field"] == "first_line_prefix"]
        assert [x["code"] for x in got] == ["structure"], record
    codes = {(x["field"], x["code"]) for x in T.revalidate(bad, fact_sheet=sheet(rec), run_date=SPOT_THU)}
    assert {("first_line_prefix", "field_mismatch"), ("captions", "field_mismatch"), ("posts", "field_mismatch"),
            ("first_line_prefix", "structure")} <= codes
    assert T.revalidate(honest, fact_sheet=sheet(rec), run_date=SPOT_THU) == []


def test_news_v22_revalidate_recomposes_from_the_frozen_switch_never_todays():
    """create_posts re-checks against `output.first_line_prefix_on` as the build froze it: a prefixed (and an
    unprefixed) output passes after a JSONB round trip; a flipped frozen flag, a non-bool flag, a missing one or a
    tampered prefix is refused; a stored news-v21 output stays `template_changed` (the deploy-window rule)."""
    rec = isp_fresh()
    for on in (True, False):
        out = T.compose(rec, run_date=SPOT_THU, store_state="live", allow_x_url=False, first_line_prefix=on)
        assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=SPOT_THU) == []
    on = compose_on(rec, SPOT_THU)
    flipped = copy.deepcopy(on)
    flipped["first_line_prefix_on"] = False
    got = {(x["field"], x["code"]) for x in T.revalidate(flipped, fact_sheet=sheet(rec), run_date=SPOT_THU)}
    assert {("captions", "field_mismatch"), ("first_line_prefix", "field_mismatch")} <= got
    for junk in ("true", 1, None):
        bad = copy.deepcopy(on)
        bad["first_line_prefix_on"] = junk
        assert T.revalidate(bad, fact_sheet=sheet(rec), run_date=SPOT_THU) == [
            {"field": "first_line_prefix_on", "code": "provenance",
             "detail": "the frozen first-line prefix switch is not a bool"}]
    missing = copy.deepcopy(on)
    missing.pop("first_line_prefix_on")
    assert {x["code"] for x in T.revalidate(missing, fact_sheet=sheet(rec), run_date=SPOT_THU)} == {"provenance"}
    tampered = copy.deepcopy(on)
    _swap_prefix(tampered, BREAKING, ALERT)          # an allowed prefix, but not the one this record composes
    assert not [x for x in T.validate_package(tampered, record=rec) if x["field"] == "first_line_prefix"]
    assert "field_mismatch" in {x["code"] for x in T.revalidate(tampered, fact_sheet=sheet(rec), run_date=SPOT_THU)}
    v21 = copy.deepcopy(on)
    v21["template_version"] = "news-v21"
    assert [x["code"] for x in T.revalidate(v21, fact_sheet=sheet(rec), run_date=SPOT_THU)] == ["template_changed"]


@pytest.mark.parametrize("factory, run_date, prefix", [(isp_fresh, SPOT_THU, BREAKING), (f13s_list2, SPOT_SAT,
                                                                                            F13_FILING),
                                                        (ceo_roundup, MON, ALERT)], ids=["isp", "f13s", "roundup"])
@pytest.mark.parametrize("platform", _EXTRA_FIELDS)
def test_news_v22_an_extra_videos_prefixed_caption_passes_its_recheck(factory, run_date, prefix, platform):
    """An EXTRA video post keeps its platform's accepted first line (`post_copy.extra_video_caption`): a prefixed
    caption is re-checked as `posts.<platform>.caption`, a prefix field — clean. An accepted caption whose prefix
    moved to its second line yields an extra caption the word scan refuses there (the derivation itself matches)."""
    rec = factory()
    out = compose_on(rec, run_date)
    caption = _video_caption(out, platform, run_date)
    assert caption.startswith(f"{prefix} ") and caption.count(prefix) == 1
    assert _problems(out, rec, run_date, platform) == []
    bad = copy.deepcopy(out)
    accepted = bad["posts"][platform]["caption"]
    head, _sep, rest = accepted.partition("\n\n")
    bad["posts"][platform]["caption"] = f"{head[len(prefix) + 1:]}\n\n{prefix} {rest}"
    codes = {(x["field"], x["code"]) for x in _problems(bad, rec, run_date, platform)}
    assert (f"video_posts.{platform}.caption", "structure") in codes, codes


def test_news_v22_the_prefix_never_costs_threads_its_picture(monkeypatch):
    """Threads posts the picture only while its composed caption fits THREADS_PICTURE_CAPTION_MAX_BYTES UTF-8 bytes
    (`script_service.THREADS_IMAGE_CAPTION_MAX_BYTES`, mirrored and pinned equal); the prefix's emoji is 4 bytes. A
    prefixed Threads caption that would pass the cap gives way to the plain headline — the picture is content, the
    prefix is not — while the other outlets keep the prefix. Shown by lowering the cap to one byte under the
    prefixed caption (no real sample sits that close: every sample keeps its prefix on Threads)."""
    from app.services.marketing import script_service as ss

    assert T.THREADS_PICTURE_CAPTION_MAX_BYTES == ss.THREADS_IMAGE_CAPTION_MAX_BYTES == 500
    rec = isp_fresh()
    honest = compose_on(rec, SPOT_THU)
    threads = honest["posts"]["threads"]["caption"]
    assert threads.startswith(f"{BREAKING} ") and len(threads.encode("utf-8")) <= 500
    monkeypatch.setattr(T, "THREADS_PICTURE_CAPTION_MAX_BYTES", len(threads.encode("utf-8")) - 1)
    out = compose_on(rec, SPOT_THU)
    assert out["captions"]["threads"] == honest["captions"]["threads"][len(BREAKING) + 1:]   # the same headline, plain
    assert out["captions"]["facebook"] == honest["captions"]["facebook"]                     # the others keep it
    assert out["captions"]["x"] == honest["captions"]["x"]
    assert T.validate_package(out, record=rec) == []


# ── news-v23: the ONE 13F list (RESTYLE_SPEC §2 items 1-2, owner 2026-11-19) ─────────────────────────────────────────
#
# "i need more companies, similar with Berkshire hathaway latest 13F. and remove what users don't care such as quarter
# before, quarter ended" / "the range from 2 to 6 are okay" / "Closed out + buy-out check": BOTH 13F series draw one
# ranked list (f13_list_berkshire.jpg, f13_list_pershing.jpg) — 2-6 rows by dollar size, the status words exactly, the
# signed change in shares, "+N more changes in the caption" — and the video cover is the list's first four rows
# (cover_13f_berkshire.jpg). No single-holding 13F post anywhere; "Closed out" everywhere a holding is no longer listed.

_F13_SAMPLES = [s for s in SAMPLES if s[0].endswith("_13f") or s[0].startswith("f13s_")]
#: Every 13F shape the list tests walk: the samples, the eight-holding spotlight (6 drawn + 2 in the caption) and the
#: roundup of eight (Berkshire's five + three more).
_F13_ALL = _F13_SAMPLES + [("f13s_list8", f13s_list8, SPOT_SAT)]
_F13_MINUS = "−"


def berkshire8():
    """Berkshire's five holdings and three more (increased / reduced): eight changes, the picture's six and "+2 more
    changes in the caption"."""
    extra = (move("OXY", "Occidental Petroleum", "increased", shares=264_000_000.0, prev_shares=255_000_000.0,
                  value_usd=13_200_000_000.0),
             move("KR", "Kroger", "decreased", shares=39_000_000.0, prev_shares=50_000_000.0,
                  value_usd=2_165_670_000.0),
             move("COF", "Capital One Financial", "decreased", shares=3_000_000.0, prev_shares=7_150_000.0,
                  value_usd=601_860_000.0))
    return berkshire(moves=berkshire().moves + extra,
                     counts=(("newly_reported", 3), ("increased", 3), ("decreased", 6), ("no_longer_reported", 2)))


_F13_ALL.append(("berkshire8_13f", berkshire8, TUE))


def _f13_ranked(rec):
    """The order the list must draw: a spotlight record's own (`R.thirteen_f_spotlight_order`), a roundup's moves by
    dollar size (`R.thirteen_f_move_flow`), unknown sizes last in the record's order."""
    if rec.series == "thirteen_f_spotlight":
        return list(rec.moves)
    return sorted(rec.moves, key=lambda m: (R.thirteen_f_move_flow(m) is None, -(R.thirteen_f_move_flow(m) or 0.0)))


def _f13_expected_row(mv):
    """(status, figure, tone sign) the row of `mv` must draw — the mock's `row_of`, with production's rounding."""
    bare = lambda v: T.shares_image(v).removeprefix("about ")              # noqa: E731
    noun = "share" if mv.move != "no_longer_reported" and round(mv.shares) == 1 else "shares"
    if mv.move == "newly_reported":
        return "New holding", "+" + bare(mv.shares)
    if mv.move == "no_longer_reported":
        return "Closed out", _F13_MINUS + bare(mv.prev_shares)
    change = bare(abs(mv.shares - mv.prev_shares))
    if mv.move == "increased":
        return f"Increased to {bare(mv.shares)} {noun}", "+" + change
    return f"Reduced to {bare(mv.shares)} {noun}", _F13_MINUS + change


@pytest.mark.parametrize("name, factory, run_date", _F13_ALL, ids=[s[0] for s in _F13_ALL])
def test_restyle_13f_both_series_draw_the_one_list(name, factory, run_date):
    """The picture of either 13F series is the mock's list: the `rows` layout, kicker "13F FILING", the title "{filer}'s
    latest 13F", the filer's plate (its logo and ticker chip) ONLY when it is listed, ONE section headed "Change in
    shares", 2-6 rows by dollar size — each the logo, the ticker chip, the name, the status ("New holding", "Closed
    out", "Increased to 40M shares", "Reduced to 20M shares") and the signed change ("+5.6M", "−14.6M": U+2212) — and
    "+N more changes in the caption" only when the record holds more; no subtitle, no fact rows, no notes, no
    quarter."""
    rec = factory()
    out = compose(rec, run_date)
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []
    spec = out["image_spec"]
    assert spec["layout"] == "rows" and spec["kicker"] == "13F FILING" == T.SERIES_SPECS[rec.series].kicker
    assert spec["title"] == (f"{T.possessive(rec.filer_name)} latest 13F" if T.possessive(rec.filer_name)
                             else f"The latest 13F of {rec.filer_name}")
    assert set(spec) == {"layout", "version", "kicker", "title", "sections", "footer"} | (
        {"header"} if rec.filer_symbol else set())
    if rec.filer_symbol:
        assert spec["header"] == {"logo": rec.filer_symbol, "name": rec.filer_name, "chip": rec.filer_symbol}
    (sec,) = spec["sections"]
    assert sec["heading"] == "Change in shares"
    ranked = _f13_ranked(rec)
    n = len(ranked)
    assert 2 <= len(sec["rows"]) == min(n, 6) <= 6
    for mv, row in zip(ranked, sec["rows"]):
        status, figure = _f13_expected_row(mv)
        assert row == {"logo": mv.company.symbol, "chip": mv.company.symbol,
                       "cells": [mv.company.name, status, figure]}, (name, row)
        assert "-" not in figure and "about" not in figure
    if n > 6:
        assert sec["more"] == f"+{n - 6} more change{'s' if n - 6 > 1 else ''} in the caption"
    else:
        assert "more" not in sec
    drawn = onscreen.image_strings(spec, out["logo_refs"])
    assert not [s for s in drawn if re.search(r"\bquarter|\bbefore\b|\bended\b|%|\$", s, re.IGNORECASE)], drawn
    assert out["image_footer"].startswith("Not investment advice · Source: SEC Form 13F · ")


def test_restyle_13f_rows_rank_by_dollar_change_unknown_last():
    """The rows rank by the move's DOLLAR size (`R.thirteen_f_move_flow`: a new holding's value, the changed shares at
    the filing's value per share, a closed-out holding's value on the quarter before's book), never by the share count
    drawn; a move with no value ranks after every sized one, in the record's order (Berkshire's Citigroup and Nu
    Holdings, as the mock) — in both series."""
    out = compose(berkshire(), TUE)
    assert [r["chip"] for r in out["image_spec"]["sections"][0]["rows"]] == ["STZ", "DPZ", "LEN", "C", "NU"]
    # Lennar's 1.8M shares are drawn below Domino's 1.3M: $250M < $549M
    sized = berkshire(moves=(move("C", "Citigroup", "no_longer_reported", shares=0.0, prev_shares=14_639_502.0,
                                  prev_value_usd=900_000_000.0),) + berkshire().moves[:3])
    assert [r["chip"] for r in compose(sized, TUE)["image_spec"]["sections"][0]["rows"]] == ["STZ", "C", "DPZ", "LEN"]
    out = compose(pershing(), TUE)
    assert [r["chip"] for r in out["image_spec"]["sections"][0]["rows"]] == ["UBER", "CMG", "CRWV"]


@pytest.mark.parametrize("name, factory, run_date", _F13_ALL, ids=[s[0] for s in _F13_ALL])
def test_restyle_13f_closed_out_replaces_no_longer_reported_everywhere(name, factory, run_date):
    """"Closed out" (owner: "Closed out + buy-out check") is the ONLY wording of a holding the latest 13F no longer
    lists — on the picture, the cover, the cards, the hook, the narration, every caption and the alt text; never "no
    longer reported / listed", "lists no … shares", "none listed", "None in this 13F", "none in this filing"; never
    "sold", "exited", "dumped"."""
    rec = factory()
    out = compose(rec, run_date)
    text = json.dumps(out, ensure_ascii=False)
    assert not re.search(r"no longer (?:report|list)|lists no\b|none listed|none in this|\bsold\b|\bexited\b|"
                         r"\bdumped\b", text, re.IGNORECASE), name
    if any(m.move == "no_longer_reported" for m in rec.moves):
        assert "closed out" in out["captions"]["facebook"]
    if any(m.move == "no_longer_reported" for m in _f13_ranked(rec)[:6]):      # a closed-out row on the picture
        assert "Closed out" in json.dumps(out["image_spec"]) and "closed out" in json.dumps(out["image_post"])


def test_restyle_13f_the_lexicon_says_closed_out_and_keeps_no_single_holding_entry():
    """No 13F lexicon entry says "no longer reported / listed", "lists no", "none listed" or "None in this 13F"; the
    single-holding entries (`f13s.h.*`, `f13s.img.*`, `f13s.fact.*`, …) and the roundup's per-kind sections
    (`f13.hook.*`, `f13.cnt.*`, `f13.ch.*`, `f13.img.*`, `f13.open.*`) are gone; the list's new strings are pinned."""
    f13 = {k: v for k, v in T.LEXICON.items() if k.startswith("f13")}
    assert not [k for k, v in f13.items() if re.search(r"no longer (?:report|list)|lists no\b|none listed|"
                                                        r"none in this", v, re.IGNORECASE)]
    gone = ("f13s.h.", "f13s.hp.fewer", "f13s.img.", "f13s.fact.", "f13s.hook.", "f13s.l2.", "f13s.c2.", "f13s.yt.",
            "f13s.p.counts.", "f13.hook.", "f13.cnt.", "f13.ch.", "f13.img.", "f13.open.", "f13.head.", "f13.lead.",
            "f13s.list.sub.", "f13s.list.open.", "f13s.list.p.new.value", "f13s.list.heading.about", "f13.kicker")
    assert not [k for k in f13 if k.startswith(gone)]
    assert {k: T.LEXICON[k] for k in ("f13s.kicker", "f13s.list.heading", "f13s.list.row.new", "f13s.list.row.gone",
                                      "f13s.list.row.more", "f13s.list.row.fewer", "f13s.list.more.one",
                                      "f13s.list.more.many")} == {
        "f13s.kicker": "13F FILING", "f13s.list.heading": "Change in shares", "f13s.list.row.new": "New holding",
        "f13s.list.row.gone": "Closed out", "f13s.list.row.more": "Increased to {shares_s} {share_noun}",
        "f13s.list.row.fewer": "Reduced to {shares_s} {share_noun}",
        "f13s.list.more.one": "+1 more change in the caption",
        "f13s.list.more.many": "+{k} more changes in the caption"}


@pytest.mark.parametrize("name, factory, run_date", _F13_ALL, ids=[s[0] for s in _F13_ALL])
def test_restyle_13f_the_cover_is_the_lists_first_rows(name, factory, run_date):
    """The video's cover (cover_13f_berkshire.jpg's idea) is the opening card's `list`: the picture's heading, its first
    F13_COVER_ROWS rows — THREE since the review of 2026-11-20 (four above the burned-caption band were ~110 px each,
    the chip squeezed beside the name; the mock's four 184 px rows run into the band) — and "+N more changes in the
    caption" for the rest of the record; its kicker and title the picture's; the filer's plate (logo + chip) only for a
    listed filer — an unlisted filer's card keeps its wordmark key (drawn only by a worker that cannot draw the list)
    and no chip; no figure. Every string it may draw is allowed by `template_onscreen.opening_strings`."""
    rec = factory()
    out = compose(rec, run_date)
    card, spec = out["opening_card"], out["image_spec"]
    n, k = len(rec.moves), T.F13_COVER_ROWS
    assert k == R.THIRTEEN_F_COVER_ROWS == 3
    assert card["kicker"] == spec["kicker"] and card["headline"] == spec["title"] and "figure" not in card
    assert card["list"]["heading"] == spec["sections"][0]["heading"]
    assert card["list"]["rows"] == spec["sections"][0]["rows"][:k]
    if n > k:
        assert card["list"]["more"] == f"+{n - k} more change{'s' if n - k > 1 else ''} in the caption"
    else:
        assert "more" not in card["list"]
    if rec.filer_symbol:
        assert card["logos"] == [rec.filer_symbol] and card["chip"] == rec.filer_symbol
    else:
        assert card["logos"] == [T.F13S_FILER_LOGO_KEY] and "chip" not in card
    assert onscreen.validate_opening_card(card, onscreen.logo_keys(out["logo_refs"])) is None
    allowed = onscreen.opening_strings(card, out["logo_refs"])
    for row in card["list"]["rows"]:
        assert set(row["cells"]) <= set(allowed) and row["chip"] in allowed
    assert card["list"]["heading"] in allowed and (card["list"].get("more") or card["kicker"]) in allowed


def test_restyle_13f_the_more_line_is_kept_by_every_caption_that_rides_it():
    """"+N more changes in the caption" is a promise: every caption that can ride the PICTURE (X, Bluesky, Threads,
    Facebook, LinkedIn) lists the holdings the picture leaves out, and every caption that can ride the VIDEO (TikTok,
    Instagram, the YouTube description) those the cover leaves out. Review 2026-11-20: a pictured X / Bluesky whose
    budget cannot keep it is no longer DROPPED (eight changes cost both outlets the post) — it is written as a TEXT
    post (the rows from the top, none required) and listed in `text_outlets`, which the freeze turns into a text
    format (``caption_list_long``): never posted beside a line it does not keep, never lost. Mutation-checked by hand
    (2026-11-19): with the per-field minimum removed (`_Parts.field_required` ignored), X and Bluesky posted the
    headline alone under a picture saying "+2 more changes in the caption" and this test went red."""
    rec = f13s_list8()
    out = compose(rec, SPOT_SAT)
    parts = _parts(rec, SPOT_SAT)
    rows = [p for p in parts.image_paragraphs if re.match(r"^[A-Z][^()]+ \([A-Z0-9-]+\): ", p)]
    assert len(rows) == 8
    assert out["image_spec"]["sections"][0]["more"] == "+2 more changes in the caption"
    assert out["opening_card"]["list"]["more"] == "+5 more changes in the caption"
    for f in ("facebook", "linkedin", "instagram", "youtube_description"):
        assert all(r in out["captions"][f] for r in rows), f
    assert all(r in out["captions"]["threads"] for r in rows[6:])
    assert all(r in out["captions"]["tiktok"] for r in rows[3:])
    assert out["dropped_outlets"] == {} and {"x", "bluesky"} <= set(out["posts"])
    assert out["text_outlets"] == ["bluesky", "x"] and out["picture_outlets"] == list(T.PICTURE_OUTLETS)
    for f in ("x", "bluesky"):
        # a text post's rows come from the TOP — never the picture's rest alone (the two smallest holdings)
        listed = [i for i, r in enumerate(rows) if r in out["captions"][f]]
        assert listed == list(range(len(listed))), (f, listed)
    # a list the picture draws whole promises nothing on the picture: X and Bluesky post their headline
    berk = compose(berkshire(), TUE)
    assert "more" not in berk["image_spec"]["sections"][0] and {"x", "bluesky"} <= set(berk["posts"])
    assert berk["text_outlets"] == []
    # … while its cover (three of five rows) still promises Citigroup and Nu Holdings to the video's captions
    paras = _parts(berkshire(), TUE).image_paragraphs
    citi = next(p for p in paras if p.startswith("Citigroup (C): "))
    nu = next(p for p in paras if p.startswith("Nu Holdings (NU): "))
    assert nu == "Nu Holdings (NU): closed out, from about 40.2 million shares the quarter before."
    for f in ("tiktok", "instagram", "youtube_description"):
        assert citi in berk["captions"][f] and nu in berk["captions"][f], f


@pytest.mark.parametrize("pictured", [[], ["bluesky", "facebook", "linkedin", "threads"]],
                         ids=["image_posts_off", "production_x_text"])
def test_a_text_x_or_bluesky_post_lists_the_rows_from_the_top_never_the_pictures_rest(pictured):
    """Review 2026-11-20 (confirmed): the "+N more" promise binds only a caption that rides the picture. Under the
    production switches (MARKETING_IMAGE_POSTS on, MARKETING_X_IMAGES off) X is a TEXT post — it never carries the
    picture whose "+2 more" it used to be held to, and was DROPPED on every 7-8 change 13F; with image posts off every
    short outlet is text. A text X / Bluesky post (`picture_outlets` without it) lists the rows from the TOP (the
    largest changes), none required — never only the rows the picture leaves out (the smallest) — and is never in
    `text_outlets` (not a fallback: its format). The frozen value round-trips through `revalidate`. Mutation-checked by
    hand: ignoring `pictured` in `_caption_bodies` turns this red."""
    for rec, day in ((f13s_list8(), SPOT_SAT), (berkshire8(), TUE)):
        out = T.compose(rec, run_date=day, store_state="live", allow_x_url=False, picture_outlets=pictured)
        assert out["picture_outlets"] == pictured and out["dropped_outlets"] == {}
        rows = [p for p in _parts(rec, day).image_paragraphs if re.match(r"^[A-Z][^()]+ \([A-Z0-9-]+\): ", p)]
        for f in ("x", "bluesky"):
            if f in pictured:
                continue
            assert f in out["posts"] and f not in out["text_outlets"], f
            listed = [i for i, r in enumerate(rows) if r in out["captions"][f]]
            assert listed == list(range(len(listed))), (f, listed)
            assert not any(r in out["captions"][f] for r in rows[T.F13_PICTURE_ROWS:]) or listed, f
        assert out["text_outlets"] == (["bluesky"] if "bluesky" in pictured else [])
        assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=day) == []
        tampered = dict(_jsonb(out), picture_outlets=list(T.PICTURE_OUTLETS))
        assert T.revalidate(tampered, fact_sheet=_jsonb(sheet(rec)), run_date=day) != []


def test_picture_outlets_is_a_checked_input():
    """`compose`'s `picture_outlets`: None = every picture platform (sorted); a string, a non-string member or a
    platform that never posts a picture is a caller bug — and `PICTURE_OUTLETS` is exactly the platforms whose format
    map allows a picture (`schemas.POST_FORMATS_BY_PLATFORM`)."""
    from app.schemas.marketing import POST_FORMATS_BY_PLATFORM
    assert set(T.PICTURE_OUTLETS) == {p for p, f in POST_FORMATS_BY_PLATFORM.items() if "image" in f}
    assert list(T.PICTURE_OUTLETS) == sorted(T.PICTURE_OUTLETS) and set(T.TEXT_PLAN_OUTLETS) <= set(T.PICTURE_OUTLETS)
    assert T.normalize_picture_outlets(None) == T.PICTURE_OUTLETS
    assert T.normalize_picture_outlets(["x", "bluesky", "x"]) == ("bluesky", "x")
    for bad, exc in (("x", TypeError), ([1], TypeError), (["tiktok"], ValueError), (["youtube"], ValueError)):
        with pytest.raises(exc):
            T.compose(berkshire(), run_date=TUE, store_state="live", allow_x_url=False, picture_outlets=bad)


def test_restyle_13f_an_extra_video_caption_must_keep_the_covers_promise():
    """An EXTRA video post (Facebook / LinkedIn / Threads) rides the video, whose cover says "+N more changes in the
    caption": its caption must list every holding the cover leaves out (`extra_video_problems`, ``cover_caption``).
    Facebook's and LinkedIn's carry every row; Threads' carries the cover's rest when it fits (Berkshire's fifth row),
    else only the picture's rest (eight changes: the cover leaves four, Threads' 500 bytes hold two) — and that extra
    video is refused. A list of four or fewer promises nothing."""
    rec = berkshire()
    out = compose(rec, TUE)
    for row in ("Citigroup (C): closed out", "Nu Holdings (NU): closed out"):
        assert row in out["posts"]["threads"]["caption"]                    # the cover's fourth and fifth rows
    for platform in ("facebook", "linkedin", "threads"):
        assert _problems(out, rec, TUE, platform) == [], platform
    eight = berkshire8()
    out8 = compose(eight, TUE)
    assert out8["opening_card"]["list"]["more"] == "+5 more changes in the caption"
    assert _problems(out8, eight, TUE, "facebook") == [] and _problems(out8, eight, TUE, "linkedin") == []
    assert {p["code"] for p in _problems(out8, eight, TUE, "threads")} == {"cover_caption"}
    four = berkshire(moves=berkshire().moves[:4])
    out4 = compose(four, TUE)
    assert out4["opening_card"]["list"]["more"] == "+1 more change in the caption"
    assert _problems(out4, four, TUE, "threads") == []                      # its fourth row is in Threads' caption
    three = berkshire(moves=berkshire().moves[:3])
    out3 = compose(three, TUE)
    assert "more" not in out3["opening_card"]["list"] and _problems(out3, three, TUE, "threads") == []


#: Eight 32-character holding names (fictional), each word under F13_MAX_WORD_CHARS.
_LONG_NAMES = ["Alpine Ridge Industrial Holdings", "Bluewater Coastal Energy Partner", "Cedar Valley Regional Bancorp Co",
               "Driftwood Specialty Chemicals Co", "Evergreen Precision Instruments", "Foxglove Consumer Brands Company",
               "Granite Peak Semiconductor Group", "Harborview Transportation Trust"]


def test_restyle_13f_a_long_caption_that_cannot_list_every_holding_keeps_the_covers_rest():
    """news-v23: Facebook, LinkedIn, Instagram and the YouTube description list EVERY holding when they fit; eight
    holdings of 32-character names and nine-digit counts, from a 60-character filer's amended filing, pass Facebook's,
    Instagram's and the description's 900 characters, so each keeps the rows the COVER leaves out (a superset of the
    picture's rest — both promises kept, the extra video too): no such outlet dropped, the post never refused for too
    few outlets. Review 2026-11-20: the cover draws three rows, so it promises FIVE of these rows — more than TikTok's
    600 characters hold at this worst case: TikTok is dropped (`over_budget`), never posted beside the cover's promise
    (ordinary names fit: `test_restyle_13f_the_more_line_is_kept_by_every_caption_that_rides_it`). Mutation-checked by
    hand (2026-11-19): with the fallback plans removed from `_caption_bodies`, Facebook, Instagram and YouTube drop
    and this composition is refused (`too_few_outlets`)."""
    assert all(len(n) <= 32 and max(len(w) for w in n.split()) <= T.F13_MAX_WORD_CHARS for n in _LONG_NAMES)
    syms = ["ALRH", "BLWC", "CDVR", "DRFT", "EVGP", "FXGL", "GRNP", "HRBV"]
    big, small = 987_654_321.0, 654_321_987.0
    kinds = [dict(F13L_FEWER, shares=small, prev_shares=big), dict(F13L_NEW, shares=big, prev_shares=0.0),
             dict(F13L_MORE, shares=big, prev_shares=small), dict(F13L_GONE, shares=0.0, prev_shares=big)] * 2
    moves = [_m(k, sym=s, name=n, **({"prev_value_usd": 9e10 - i * 1e9} if k["kind"] == "no_longer_reported"
                                     else {"value_usd": 9e10 - i * 1e9}))
             for i, (k, s, n) in enumerate(zip(kinds, syms, _LONG_NAMES))]
    filer = "Northwind Fabrikam Capital Management International Advisors"
    assert len(filer) == 60
    rec = f13_list(moves, changes=31, filer=filer, amended_on=date(2026, 11, 18), filed=date(2026, 11, 12))
    out = compose(rec, SPOT_SAT)
    order = [m.company.symbol for m in rec.moves]
    assert [r["chip"] for r in out["image_spec"]["sections"][0]["rows"]] == order[:6]
    k = T.F13_COVER_ROWS
    assert [r["chip"] for r in out["opening_card"]["list"]["rows"]] == order[:k]
    listed = [s for s in order if f"({s}):" in out["captions"]["linkedin"]]
    assert listed == order                                                   # LinkedIn's 1,200 characters: every row
    for f in ("facebook", "instagram", "youtube_description"):
        listed = [s for s in order if f"({s}):" in out["captions"][f]]
        assert listed == order[k:], (f, listed)                               # the cover's rest, nothing more
    for platform in ("facebook", "linkedin", "instagram", "youtube"):
        assert platform in out["posts"], platform
    assert [d["code"] for d in out["dropped_outlets"]["tiktok"]] == ["over_budget"] and "tiktok" not in out["posts"]
    assert _problems(out, rec, SPOT_SAT, "facebook") == [] and _problems(out, rec, SPOT_SAT, "linkedin") == []


def test_restyle_13f_never_a_single_holding_post():
    """Owner 2026-11-19: "the range from 2 to 6 are okay" — no single-holding 13F post anywhere: both records refuse
    one move, and the composer refuses a forged one (`too_few_rows`) before it writes a word."""
    with pytest.raises(ValueError, match="moves"):
        f13_list([_m(F13L_FEWER)])
    with pytest.raises(ValueError, match="moves"):
        berkshire(moves=berkshire().moves[:1])
    for rec in (f13s_list2(), berkshire()):
        object.__setattr__(rec, "moves", rec.moves[:1])
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(rec, SPOT_SAT if rec.series == "thirteen_f_spotlight" else TUE)
        assert e.value.code == "too_few_rows"
    assert R.THIRTEEN_F_LIST_MIN_MOVES == 2 and T.F13_PICTURE_ROWS == 6 and T.F13_COVER_ROWS == 3


@pytest.mark.parametrize("name, factory, run_date", _F13_ALL, ids=[s[0] for s in _F13_ALL])
def test_restyle_13f_share_counts_only_and_no_person(name, factory, run_date):
    """Share counts only: no dollar figure (a new holding's filed value is no longer written), no %, no price, no trade
    verb anywhere a person reads — the hook, the narration, the cards, the cover, the picture, the alt text and every
    caption body; the subject is the filing ENTITY (no person), every narration line at least four words."""
    rec = factory()
    out = compose(rec, run_date)
    public = json.dumps({k: out[k] for k in ("hook", "video_script", "cards", "opening_card", "image_spec",
                                             "image_post", "captions")}, ensure_ascii=False)
    assert "$" not in public and "%" not in public, name
    assert not re.search(r"\bprice|\bbought\b|\bbuys?\b|\bsells?\b|\badded\b|\btrimmed\b|\bcut\b|new position",
                         public, re.IGNORECASE), name
    assert out["persons"] == [] and not T.OFF_TABLE_VERBS_RE.search(public)
    assert all(len(line.split()) >= 4 for line in out["video_script"])
    assert T.spoken_company(rec.filer_name).split()[0] in out["hook"] or str(len(rec.moves)) in out["hook"]


@pytest.mark.parametrize("name, factory, run_date", _F13_ALL, ids=[s[0] for s in _F13_ALL])
def test_restyle_13f_the_narration_says_what_the_list_shows(name, factory, run_date):
    """The video says what the picture shows, its own way: one line per kind in row order (its holdings named, the
    first three kinds) — "it reported new holdings of …", "it also closed out its holdings of …" — then what a 13F shows
    and the 13F explainer; its cards are each kind's title over the same names. A list of one kind narrates the
    filing's quarter third."""
    rec = factory()
    out = compose(rec, run_date)
    kinds = list(dict.fromkeys(m.move for m in _f13_ranked(rec)))
    verbs = {"newly_reported": "new", "no_longer_reported": "closed out", "increased": "increased",
             "decreased": "reduced"}
    titles = {"newly_reported": ("New holding", "New holdings"), "no_longer_reported": ("Closed out",),
              "increased": ("Increased",), "decreased": ("Reduced",)}
    for i, kind in enumerate(kinds[:3]):
        assert verbs[kind] in out["video_script"][i], (name, i, out["video_script"][i])
        assert out["cards"][i]["title"] in titles[kind]
    assert out["video_script"][3] in (T.LEXICON["f13.l4.explainer"], T.LEXICON["f13.l4.explainer.short"])
    if len(kinds) == 1:
        assert out["cards"][2]["title"] == "Quarter ended"
    assert out["hook"] and T.HOOK_WORDS[0] <= len(out["hook"].split()) <= T.HOOK_WORDS[1]


def test_restyle_13f_no_art_and_no_data_card_for_either_series():
    """A list is several companies: neither 13F series ever carries per-post art, and neither keeps a data card — the
    video's cover IS the list (a data card would draw it again)."""
    from app.services.marketing import post_art
    assert {"thirteen_f", "thirteen_f_spotlight"} <= post_art.NEVER_ART_SERIES
    assert not {"thirteen_f", "thirteen_f_spotlight"} & set(post_art.ART_SERIES)
    for name, factory, run_date in _F13_SAMPLES:
        rec = factory()
        out = compose(rec, run_date)
        assert post_art.art_subject_for(rec.series, out, rec) is None, name
        picture = onscreen.image_strings(out["image_spec"], out["logo_refs"])
        assert onscreen.data_card_slot(rec.series, out["cards"], picture) is None, name
    assert {"thirteen_f", "thirteen_f_spotlight"} <= set(onscreen.NO_DATA_CARD_SERIES)


@pytest.mark.parametrize("kinds, clauses", [
    (("decreased", "decreased"), "reduced 2 holdings"),
    (("increased",) * 3, "increased 3 holdings"),
    (("newly_reported", "no_longer_reported"), "reported 1 new holding and closed out 1"),
    (("no_longer_reported", "newly_reported"), "closed out 1 holding and reported 1 new one"),
    (("decreased", "increased", "newly_reported"), "reduced 1 holding, increased 1, and reported 1 new one"),
    (("decreased", "increased", "newly_reported", "no_longer_reported"),
     "reduced 1 holding, increased 1, reported 1 new one, and closed out 1"),
], ids=["fewer2", "more3", "new_gone", "gone_new", "three_kinds", "four_kinds"])
def test_restyle_13f_list_hook_grammar_for_every_count(kinds, clauses):
    """The hook of a list that is every change its filing reports: the verbs in row order — "closed out" for a holding no
    longer listed — the first with its noun, "one" / "ones" after it, an Oxford comma from three kinds; the after-prefix
    twin says the same clauses with the filer as their subject."""
    rec = _f13l_kinds(kinds, changes=len(kinds))
    parts = _parts(rec, SPOT_SAT)
    assert parts.hook[0] == f"Fabrikam Capital's latest 13F: it {clauses}."
    assert parts.after_prefix[parts.image_headlines[0]] == f"Fabrikam Capital {clauses}."
    out = compose(rec, SPOT_SAT)
    assert T.HOOK_WORDS[0] <= len(out["hook"].split()) <= T.HOOK_WORDS[1] and out["hook"] in parts.hook


#: A direction word a list hook names.
_F13L_DIRECTION_RE = re.compile(r"\b(?:reduced|increased|new|closed out)\b")


def _f13l_kinds(kinds, **kw):
    """A list of `kinds`, in that order (each half the dollar size of the one before), its filing's counts unknown
    unless `changes` is given."""
    base = {"decreased": F13L_FEWER, "increased": F13L_MORE, "newly_reported": F13L_NEW,
            "no_longer_reported": F13L_GONE}
    value, moves = 5e9, []
    for i, kind in enumerate(kinds):
        spec = base[kind]
        sym, name = f"{spec['sym'][:3]}{'ABCDE'[i]}", f"{spec['name']} {'ABCDE'[i]}"
        over = {"prev_value_usd": value} if kind == "no_longer_reported" else {"value_usd": value}
        if kind in ("decreased", "increased"):
            over["value_usd"] = value * spec["shares"] / abs(spec["shares"] - spec["prev_shares"])
        if kind == "newly_reported":
            over["listed_on"] = None
        moves.append(_m(spec, sym=sym, name=name, **over))
        value /= 2
    rec = f13_list(moves, **kw)
    assert [m.move for m in rec.moves] == list(kinds)
    return rec


@pytest.mark.parametrize("kinds, filer, hook", [
    (("decreased", "increased"), "Fabrikam Capital",
     "2 of the holding changes in Fabrikam Capital's latest 13F: 1 reduced, 1 increased."),
    (("decreased", "decreased", "no_longer_reported"), "Fabrikam Capital",
     "3 of the holding changes in Fabrikam Capital's 13F: 2 reduced, 1 closed out."),
    (("decreased", "newly_reported", "increased", "no_longer_reported", "decreased"), "Fabrikam Capital",
     "5 of Fabrikam Capital's holding changes, including 2 reduced and 1 new."),
    (("no_longer_reported", "increased", "newly_reported"), "Pershing Square Capital Management",
     "3 of Pershing Square Capital Management's holding changes, including 1 closed out."),
], ids=["two_kinds", "three_kinds_fit", "four_kinds_including", "long_filer_first_kind"])
def test_restyle_13f_some_hook_leads_with_the_direction(kinds, filer, hook):
    """A list that is NOT every change its filing reports: "N of …" (never the filing's counts) with the ROWS' counts and
    their direction words — "closed out" among them — all of them where they fit HOOK_WORDS, else the first kinds'."""
    rec = _f13l_kinds(kinds, filer=filer)
    assert not R.thirteen_f_spotlight_lists_every_change(rec)
    out = compose(rec, SPOT_SAT)
    assert out["hook"] == hook and _F13L_DIRECTION_RE.search(hook)
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("name, factory, run_date", _F13_SAMPLES, ids=[s[0] for s in _F13_SAMPLES])
def test_restyle_13f_counts_read_as_the_filings_only_when_they_are(name, factory, run_date):
    """Never imply the rows are every change when they are not: unless a SPOTLIGHT's filing counts sum to exactly its
    rows (`thirteen_f_spotlight_lists_every_change`; never the roundup's), the hook, every caption's first line, the
    YouTube title and the alt text say "N of …"; never "largest" / "biggest" / "top"."""
    rec = factory()
    out = compose(rec, run_date)
    n = len(rec.moves)
    every = rec.series == "thirteen_f_spotlight" and R.thirteen_f_spotlight_lists_every_change(rec)
    firsts = [out["hook"]] + [b.split("\n", 1)[0] for b in out["captions"].values()] + [
        out["image_post"]["paragraphs"][0]]
    some = [f for f in firsts if f"{n} of " in f]
    assert (some == []) if every else (some == firsts), (name, firsts)
    assert not re.search(r"\blargest\b|\bbiggest\b|\btop\b", json.dumps(out, ensure_ascii=False), re.IGNORECASE)


def test_restyle_13f_a_listed_filers_plate_and_an_of_filer():
    """A filer with its own listing heads the picture and the cover with its OWN logo and ticker (the plate); a filer
    name already ending in a possessive takes the ".of" twin everywhere ("The latest 13F of Litware's")."""
    rec = f13_list([_m(F13L_FEWER), _m(F13L_MORE)], filer="Litware's", filer_symbol="LWSC", changes=4)
    out = compose(rec, SPOT_SAT)
    assert T.validate_package(out, record=rec) == []
    assert out["image_spec"]["header"] == {"logo": "LWSC", "name": "Litware's", "chip": "LWSC"}
    assert out["opening_card"]["logos"] == ["LWSC"] and out["opening_card"]["chip"] == "LWSC"
    assert out["image_spec"]["title"] == out["opening_card"]["headline"] == "The latest 13F of Litware's"
    assert "Litware's's" not in json.dumps(out) and "Litware's'" not in json.dumps(out)
    assert out["hook"] == "2 of the holding changes in the 13F of Litware's: 1 reduced, 1 increased."


def test_restyle_13f_validate_refuses_another_layout_and_a_forged_cover():
    """The validator ties both 13F series to the list (`rows`): another layout is refused; a cover list of six rows (owner
    decisions 2026-11-20: MAX_OPENING_ROWS is five, the Form 4 lists' compact all-rows cover), or one with an unknown
    key, is `opening_card_invalid`."""
    rec = f13s_list5()
    out = compose(rec, SPOT_SAT)
    bad = copy.deepcopy(out)
    bad["image_spec"]["layout"] = "spotlight"
    assert {v["code"] for v in T.validate_package(bad, record=rec)} & {"image_spec_invalid", "schema"}
    six = copy.deepcopy(out)
    rows5 = out["image_spec"]["sections"][0]["rows"][:5]
    six["opening_card"]["list"]["rows"] = rows5 + rows5[:1]
    assert onscreen.MAX_OPENING_ROWS == 5 and len(six["opening_card"]["list"]["rows"]) == 6
    assert "opening_card_invalid" in {v["code"] for v in T.validate_package(six, record=rec)}
    odd = copy.deepcopy(out)
    odd["opening_card"]["list"]["note"] = "Quarter ended Sep 30, 2026"
    assert "opening_card_invalid" in {v["code"] for v in T.validate_package(odd, record=rec)}


# ══ final review 2026-11-20: the two 13F template guards whose tests went out with the old 13F layout ═══════════════
#
# `_f13_checked` (both 13F series) refuses a holding that IS the filer and a name word too long to draw whole; the
# compose step keeps a backstop for the filer's logo key. Their only tests (`test_a_filer_symbol_that_is_also_a_holding_
# is_refused`, `test_a_name_word_too_long_to_draw_whole_is_refused`) were deleted with the old layout's tests; with
# both guards removed every suite stayed green. Restored against the news-v23 list, each pinned by its DETAIL (the
# backstop gives the same code), each mutation-checked by hand (2026-11-20).


def test_final_a_holding_that_is_the_filer_itself_is_refused():
    """The filer's own stock on its own 13F — by its listed symbol, or a holding whose name folds to the filer's — is
    `record_invalid` before anything composes: the picture would put the filer's identity (its plate) on a holding row.
    Reachable for a listed filer whose 13F lists its own shares (asset managers and banks often do)."""
    base = berkshire()
    by_symbol = berkshire(filer_symbol="STZ")                                  # STZ is one of its holdings
    by_name = berkshire(moves=(move("BRKC", "Berkshire Hathaway", "newly_reported", shares=5_624_324.0,
                                    prev_shares=0.0, value_usd=1_240_000_000.0), *base.moves[1:]))
    for rec in (by_symbol, by_name):
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(rec, TUE)
        assert (e.value.code, e.value.detail) == ("record_invalid", "a holding is the filer itself")
    assert T.validate_package(compose(base, TUE), record=base) == []           # anti-vacuity: the real record composes


def test_final_the_filers_logo_key_backstop_still_refuses_a_holding_keyed_like_the_filer(monkeypatch):
    """The compose step's own backstop, behind `_f13_checked`: with the per-move check bypassed, a holding keyed by the
    filer's logo key would be named the FILER on the picture (its logo ref resolved to "Berkshire Hathaway") — refused
    `record_invalid`, never composed."""
    monkeypatch.setattr(T, "_f13_checked", lambda rec, run_date: None)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(berkshire(filer_symbol="STZ"), TUE)
    assert (e.value.code, e.value.detail) == ("record_invalid", "the filer's logo key is also a holding")


@pytest.mark.parametrize("where", ["filer", "holding"])
def test_final_a_13f_name_word_too_long_to_draw_whole_is_refused(where):
    """A word longer than F13_MAX_WORD_CHARS (20) in the filer's or a holding's name is `slot_rejected` (one post lost)
    instead of a CardOverflow that skips the DAY at render after the build was accepted. For the FILER this check is
    the only bound (`filer_name_problem` bounds the name's length, not its words); a holding's `CompanyRef` refuses such
    a name itself, so it is built UNCHECKED here and the template's refusal still stands behind it. A 20-character word
    composes. The layout suite shows the bound matters (`test_final_a_13f_filer_word_past_the_bound_overflows_…`)."""
    word = "Biopharmaceuticalsxyz"                                                 # 21 characters
    assert len(word) == T.F13_MAX_WORD_CHARS + 1 == R.COMPANY_NAME_WORD_MAX_CHARS + 1
    base = berkshire()
    mv = base.moves[0]
    if where == "filer":
        rec, fits = berkshire(filer_name=f"{word} Capital"), berkshire(filer_name=f"{word[:-1]} Capital")
    else:
        rec = berkshire(moves=(replace(mv, company=_unchecked(mv.company, name=f"{word} Inc")), *base.moves[1:]))
        fits = berkshire(moves=(replace(mv, company=co(mv.company.symbol, f"{word[:-1]} Inc")), *base.moves[1:]))
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, TUE)
    assert e.value.code == "slot_rejected" and "20 characters" in e.value.detail
    assert T.validate_package(compose(fits, TUE), record=fits) == []


# ══ news-v23 phase 3, the theme's last three angles (2026-11-20): growth + profit, the insider scorecard, Congress ════

_TABLE_SAMPLES = {"growth_profit": silicon_growth, "insiders": cyber_insiders, "congress": battlefield_congress}


@pytest.mark.parametrize("value, image, words", [
    (1.14, "+114%", "114% higher than the year before"),
    (0.004, "0%", "about the same as the year before"),
    (-0.004, "0%", "about the same as the year before"),
    (0.005, "+1%", "1% higher than the year before"),                 # half-up
    (-0.05, "−5%", "5% lower than the year before"),                  # U+2212, never a hyphen
    (5.0, "+500%", "500% higher than the year before"),
    (12.345, "+1,235%", "1,235% higher than the year before"),
])
def test_phase3_growth_is_a_signed_whole_percent_on_the_picture_and_words_elsewhere(value, image, words):
    assert T.growth_image(value) == image and T.growth_words(value) == words
    assert onscreen.GROWTH_FIGURE_RE.fullmatch(image)


@pytest.mark.parametrize("value, image, long, short", [
    (56.07, "$56", "$56 of profit per $100 of revenue", "$56 profit per $100"),
    (-17.0, "−$17", "a loss of $17 per $100 of revenue", "$17 loss per $100"),
    (0.4, "$0.40", "$0.40 of profit per $100 of revenue", "$0.40 profit per $100"),
    (-0.996, "−$1", "a loss of $1 per $100 of revenue", "$1 loss per $100"),
    (0.0, "$0", "no profit and no loss", "no profit and no loss"),
    (-1000.0, "−$1,000", "a loss of $1,000 per $100 of revenue", "$1,000 loss per $100"),
])
def test_phase3_profit_per_100_is_whole_dollars_or_cents_never_a_false_zero(value, image, long, short):
    assert T.profit_image(value) == image and onscreen.PROFIT_FIGURE_RE.fullmatch(image)
    assert T.profit_words(value) == long and T.profit_words(value, short=True) == short
    with pytest.raises(T.NewsTemplateRefused) as e:
        T.profit_image(0.004)                                          # would print "$0.00": refused, never zero
    assert e.value.code == "implausible_figures"


@pytest.mark.parametrize("angle", sorted(_TABLE_SAMPLES))
def test_phase3_each_table_angle_draws_its_own_layout_footer_and_cover(angle):
    rec = _TABLE_SAMPLES[angle]()
    out = compose(rec, THEME_WED)
    spec, card = out["image_spec"], out["opening_card"]
    assert spec["layout"] == T.THEME_LAYOUT_OF_ANGLE[angle] == layout_of(rec)
    assert T.validate_package(out, record=rec) == []
    assert out["source_ref"] == f"news:theme_explainer:{rec.slug}:{angle}:2026-11-25"
    # the cover IS the picture's list: its first rows, in the picture's own layout, under the picture's sub line
    lst = card["list"]
    assert lst["layout"] == spec["layout"] and lst["heading"] == spec["subtitle"] and lst["note"] == spec["note"]
    assert lst["rows"] == spec["rows"][:T.THEME_TABLE_COVER_ROWS] and T.THEME_TABLE_COVER_ROWS == 6
    assert card["headline"] == rec.title and card["logos"] == [spec["rows"][0]["logo"]]
    n = len(rec.members)
    if n > len(lst["rows"]):
        assert lst["more"].startswith(f"+{n - len(lst['rows'])} more")
    # a theme post draws no data card (its cover is the list)
    assert onscreen.data_card_slot(out["series"], out["cards"], []) is None
    # never a person's name, a price word or a vendor
    blob = json.dumps(out, ensure_ascii=False)
    assert not re.search(r"\b(?:bought|sold|price|FMP|Financial Modeling Prep|beat|miss|expected|top|best|leader)\b",
                         blob, re.I)


def test_phase3_the_growth_post_reads_like_the_mock():
    out = compose(silicon_growth(), THEME_WED)
    spec = out["image_spec"]
    assert spec["subtitle"] == "12 of its 21 companies · revenue growth and profit · latest fiscal year"
    assert spec["heads"] == [["Revenue growth"], ["Profit per $100", "of revenue"]]
    assert spec["note"] == "Profit = net income; growth = revenue vs the year before" and spec["icon"] == "wafer"
    assert spec["more"] == "+2 more" and len(spec["rows"]) == 10
    # the THEME's order (no ranking word), each figure in its shape; the bar in proportion to the largest growth shown
    assert [r["logo"] for r in spec["rows"]] == [s for s, *_ in SILICON_GROWTH[:10]]
    nvda, mrvl = spec["rows"][0], spec["rows"][7]
    assert (nvda["growth"], nvda["ratio"], nvda["profit"]) == ("+114%", 1.0, "$56")
    assert (mrvl["growth"], mrvl["profit"]) == ("+5%", "−$17")
    assert out["image_footer"] == "Not investment advice · Source: company financial statements · fiscal 2025 or 2026"
    # every listed company has its caption paragraph with its figures (the long captions); the picture's "+2 more"
    # are listed where the picture rides (X / Bluesky / Threads), in the short form
    # a long caption that cannot hold every full paragraph lists EVERY company in the short form, figures with it
    for field in ("facebook", "linkedin", "instagram", "youtube_description"):
        cap = out["captions"][field]
        for s, n, *_ in SILICON_GROWTH:
            assert re.search(rf"\n{re.escape(n)}: (?:fiscal 20\d\d )?revenue ", cap), (field, n)
    assert "Intel: revenue −2%, $19 loss per $100" in out["captions"]["facebook"]
    # the full paragraph, where the budget holds every company's (four companies)
    small = compose(silicon_growth(rows=SILICON_GROWTH[8:12]), THEME_WED)
    assert "Intel: fiscal 2025 revenue 2% lower than the year before; a loss of $19 per $100 of revenue." in \
        small["captions"]["facebook"]
    assert out["captions"]["threads"].endswith("Intel: revenue −2%, $19 loss per $100\n\nTexas Instruments: revenue +6%, "
                                               "$28 profit per $100") and not out["dropped_outlets"]


def test_phase3_the_insider_scorecard_reads_like_the_mock_and_speaks_class_c():
    rec = cyber_insiders()
    out = compose(rec, THEME_WED)
    spec = out["image_spec"]
    assert spec["subtitle"] == "15 companies · insider filings · last 90 days"
    assert spec["heads"] == [["Disclosed", "purchases"], ["Disclosed", "sales"]]
    # never "open-market" (compliance F2: code P / S is "open market or private" — the mock's note reworded)
    assert spec["note"] == "Purchases and sales only · sales can be pre-planned (10b5-1)" and spec["icon"] == "shield"
    # purchases first (largest first), then sales by size; "—" where none was filed; "+1 more with filings"
    assert [(r["logo"], r["buys"], r["sells"]) for r in spec["rows"][:4]] == [
        ("RPD", "$2.1M", "—"), ("S", "$1.2M", "$8.6M"), ("TENB", "$640K", "—"), ("CRWD", "—", "$186M")]
    assert spec["more"] == "+1 more with filings" and len(spec["rows"]) == 10
    assert out["image_footer"] == "Not investment advice · Source: SEC Form 4 · filed Aug 27–Nov 24, 2026"
    assert "Varonis: about $9.8 million of disclosed sales." in out["captions"]["facebook"]
    # 15 checked, 11 listed: the other four said so, honestly
    assert "No purchase or sale of the stock was disclosed at the other 4 companies checked." in \
        out["captions"]["linkedin"]
    # fewer checked than the theme holds: the sub line says "{c} of its {m}", the captions say who is not included
    out2 = compose(cyber_insiders(rows=CYBER_INSIDERS[:4], companies_checked=13), THEME_WED)
    assert out2["image_spec"]["subtitle"] == "13 of its 15 companies · insider filings · last 90 days"
    assert "2 of its 15 companies are not included" in out2["captions"]["linkedin"]
    assert R.record_content_classes(rec) == frozenset({"C", "F"})


def test_fix_the_insider_scorecard_never_gives_a_reason_for_the_companies_it_left_out():
    """Fix round 2026-11-20: a 21-company theme (Silicon Rush's size) reads at most 16 members
    (`THEME_INSIDER_LOOKUPS`), and the left-out ones mix members never read (past the lookups, a name the table cannot
    draw, an ADR) with reads that could not be used — so the caption states the count and NO reason (the old "their
    filings for the period could not be fully checked" was false for a member never read)."""
    out = compose(cyber_insiders(rows=CYBER_INSIDERS[:4], companies_checked=16, theme_size=21), THEME_WED)
    assert out["image_spec"]["subtitle"] == "16 of its 21 companies · insider filings · last 90 days"
    text = "\n".join(out["captions"].values())
    assert "5 of its 21 companies are not included." in out["captions"]["linkedin"]
    assert "could not be" not in text and "checked." not in text.replace("companies checked.", "")
    assert T.LEXICON["th.in.p.unchecked"] == "{u} of its {m} companies are not included."


def test_phase3_the_congress_post_reads_like_the_mock_and_names_no_member():
    out = compose(battlefield_congress(), THEME_WED)
    spec = out["image_spec"]
    assert (spec["subtitle"], spec["unit"], spec["icon"]) == ("Members of Congress who disclosed purchases", "members",
                                                              "radar")
    assert [(r["logo"], r["count"]) for r in spec["rows"]] == [("PLTR", "6"), ("LMT", "4"), ("RTX", "3"), ("GD", "2"),
                                                               ("KTOS", "2")]
    assert spec["note"] == "October 2026 · each member counted once per company" and "more" not in spec
    assert out["image_footer"] == "Not investment advice · Source: congressional disclosures · October 2026"
    assert out["video_script"][0] == "6 members of Congress disclosed purchases of Palantir stock in October 2026."
    blob = json.dumps(out, ensure_ascii=False)
    assert not re.search(r"\b(?:senators?|representatives?|republican|democrat|party|house|sales?|\$\d)", blob, re.I)


@pytest.mark.parametrize("field, value, code", [
    # the growth figure's "%" stands only in its own cell and its own phrase, on the growth post only
    ("cards.0.body", "Revenue +14% from the year before", "structure"),
    ("cards.0.body", "Shares were 14% higher than the year before", "structure"),          # a price phrase
    ("cards.0.body", "The stock +14%, a move", "structure"),
    ("video_script.2", "Shares rose 14% in the year before the report, a big move.", "structure"),
])
def test_phase3_the_growth_percent_exemption_is_positional_with_must_reject_twins(field, value, code):
    rec = silicon_growth()
    out = copy.deepcopy(compose(rec, THEME_WED))
    node, *path = field.split(".")
    if node == "cards":
        out["cards"][int(path[0])][path[1]] = value
    else:
        out[node][int(path[0])] = value
    assert code in {v["code"] for v in T.validate_package(out, record=rec)}


def test_phase3_the_growth_phrases_are_exempt_only_on_a_growth_post():
    # the same phrase on a market-value post (another layout) is a bare "%"
    rec = power_machine()
    out = copy.deepcopy(compose(rec, THEME_WED))
    out["cards"][2]["body"] = "Revenue 14% higher than the year before"
    assert "structure" in {v["code"] for v in T.validate_package(out, record=rec)}
    # and the growth cell's shape at another path of a growth post is not exempt
    rec = silicon_growth()
    out = copy.deepcopy(compose(rec, THEME_WED))
    out["image_spec"]["subtitle"] = "+14%"
    assert "structure" in {v["code"] for v in T.validate_package(out, record=rec)}
    # the exempt phrase as the template writes it passes
    assert T._GROWTH_PHRASE_RE.search("NVIDIA: fiscal 2026 revenue 114% higher than the year before; …")
    assert not T._GROWTH_PHRASE_RE.search("shares 114% higher than the year before")


@pytest.mark.parametrize("make, field, value, code", [
    (cyber_insiders, "cards.0.body", "Rapid7 insiders bought $2.1M", "off_table_verb"),
    (cyber_insiders, "cards.1.body", "CrowdStrike insiders sold stock", "off_table_verb"),
    (battlefield_congress, "cards.0.body", "6 members bought Palantir", "off_table_verb"),
    (battlefield_congress, "cards.0.body", "Senate members disclosed purchases", "congress_narrowing"),
    (battlefield_congress, "cards.0.body", "Their sales were disclosed too", "congress_wording"),
    (battlefield_congress, "cards.0.body", "6 members disclosed $1M of purchases", "congress_wording"),
])
def test_phase3_the_filing_angles_speak_the_class_c_verb_table(make, field, value, code):
    rec = make()
    out = copy.deepcopy(compose(rec, THEME_WED))
    _node, i, k = field.split(".")
    out["cards"][int(i)][k] = value
    assert code in {v["code"] for v in T.validate_package(out, record=rec)}


def test_phase3_a_theme_post_draws_its_records_angle_and_no_other():
    rec = cyber_insiders()
    out = copy.deepcopy(compose(rec, THEME_WED))
    out["image_spec"]["layout"] = "figures"
    # the angle's own layout is checked by name (field "image_spec.layout"), not only by the walk that also fails
    assert ("image_spec.layout", "image_spec_invalid") in {(v["field"], v["code"])
                                                           for v in T.validate_package(out, record=rec)}


@pytest.mark.parametrize("make, code", [
    (lambda: silicon_growth(rows=[("NVDA", "Wmwmwmwm Wmwmwmwm Wmwmwmwm", 120, 100, 10, "2025")] + SILICON_GROWTH[1:]),
     "slot_rejected"),                                                   # a name the table cannot draw
    (lambda: silicon_growth(rows=[("NVDA", "NVIDIA", 120, 100, 10, "2021")] + SILICON_GROWTH[1:]), "stale_source"),
    (lambda: silicon_growth(posted_on=THEME_WED - timedelta(days=7)), "record_invalid"),       # another run's record
    (lambda: cyber_insiders(posted_on=THEME_WED + timedelta(days=1), window_start=THEME_WED - timedelta(days=89),
                            window_end=THEME_WED), "record_invalid"),
    (lambda: cyber_insiders(rows=CYBER_INSIDERS[:2] + [("TENB", "Tenable", 0.4, 0.0)] + CYBER_INSIDERS[3:]),
     "implausible_figures"),                                                              # $0.40 would print "$0"
    (lambda: battlefield_congress(rows=[("PLTR", "Wmwmwmwmwmwmwmwm Wmwmwmwmwmwmwmw", 6)]
                                  + BATTLEFIELD_CONGRESS[1:]), "slot_rejected"),
])
def test_phase3_a_table_record_the_post_cannot_say_is_refused(make, code):
    rec = make()
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, THEME_WED)
    assert e.value.code == code


def test_phase3_the_growth_footer_names_its_rows_years_one_two_or_a_span():
    one = [("NVDA", "NVIDIA", 120, 100, 10, "2025")] * 0 + [(s, n, r, p, ni, "2025") for s, n, r, p, ni, _ in
                                                            SILICON_GROWTH]
    assert compose(silicon_growth(rows=one), THEME_WED)["image_footer"].endswith("· fiscal 2025")
    span = [(s, n, r, p, ni, ("2024" if i == 3 else fy)) for i, (s, n, r, p, ni, fy) in enumerate(SILICON_GROWTH)]
    assert compose(silicon_growth(rows=span), THEME_WED)["image_footer"].endswith("· fiscal 2024 to 2026")


# ── phase 4 (2026-11-20): a WALKED-BACK segment fact is always tagged and never sets the sub line's years ─────────────

def test_p4_a_walked_back_fact_is_always_tagged_even_inside_the_sub_lines_years():
    """`ThemeMember.latest_fiscal_year` (the adapter walked a gross newest stack back): the fact is never its company's
    latest year, so its tile ALWAYS draws its own year — also early in a year, when the sub line's years would cover it
    — and it never widens the sub line ("fiscal 2026", not "fiscal 2025 or 2026"). Control: the same fact without the
    field is drawn untagged inside "fiscal 2025 or 2026"."""
    run = date(2027, 3, 3)                                         # before THEME_FACT_LATE_MONTH: two years untagged
    rows = [(s, n, "Data Center", 0.5, "2026") for s, n in (
        ("NVDA", "NVIDIA"), ("AVGO", "Broadcom"), ("AMD", "AMD"), ("MRVL", "Marvell Technology"),
        ("ARM", "Arm Holdings"), ("MU", "Micron Technology"), ("CDNS", "Cadence Design Systems"))]

    def rec(latest):
        members = tuple(R.ThemeMember(company=co(s, n), top_segment=g, top_segment_share=sh, fiscal_year=fy)
                        for s, n, g, sh, fy in rows) + (
            R.ThemeMember(company=co("INTC", "Intel"), top_segment="Client Computing Group", top_segment_share=0.55,
                          fiscal_year="2025", latest_fiscal_year=latest),)
        return R.ThemeExplainer(series="theme_explainer", slug="silicon-rush", title="Silicon Rush", members=members,
                                tickers_as_of=date(2027, 2, 20), theme_size=8, angle="make_money", posted_on=run)

    walked = compose(rec("2026"), run)
    tiles = {t["name"]: t.get("line") for t in walked["image_spec"]["tiles"]}
    assert tiles["Intel"] == "Client Computing, FY2025 · 55% of revenue"
    assert walked["image_spec"]["subtitle"].endswith("fiscal 2026")
    assert "Intel: Client Computing Group, 55% of fiscal 2025 revenue." in walked["captions"]["linkedin"]
    plain = compose(rec(None), run)
    tiles = {t["name"]: t.get("line") for t in plain["image_spec"]["tiles"]}
    assert tiles["Intel"] == "Client Computing · 55% of revenue"
    assert plain["image_spec"]["subtitle"].endswith("fiscal 2025 or 2026")


def test_p4_intels_filed_segment_name_draws_the_mocks_short_label():
    """theme_ai_chips_more.jpg: "Client Computing, FY2022" — FMP files Intel's line as "Client Computing Group"
    (tests/test_revenue_breakdown_reconciliation.py's INTC rows); the tile draws the code-owned short label, the
    captions keep the filed name."""
    assert T.THEME_SEGMENT_SHORT_LABELS["Client Computing Group"] == "Client Computing"
    rows = [r if r[0] != "INTC" else ("INTC", "Intel", "Client Computing Group", 0.55, "2022") for r in AI_CHIPS]
    out = compose(theme(rows, theme_size=14), THEME_RUN)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["Intel"] == "Client Computing, FY2022 · 55% of revenue"
    assert "Intel: Client Computing Group, 55% of fiscal 2022 revenue." in out["captions"]["linkedin"]


def test_p4_a_walked_back_fact_newer_than_every_current_fact_is_kept_tagged_and_never_dates_the_sub_line():
    """A company whose fiscal year ends in January (fiscal 2026 ended Jan 2026, gross) walked back to fiscal 2025, beside
    companies whose latest facts are fiscal 2024, at a March 2026 run: the walked-back fact is KEPT (tagged), and the
    sub line still states only the current facts' year ("fiscal 2024") — the walked-back year never sets it."""
    run = date(2026, 3, 4)
    rows = [(s, n, "Data Center", 0.5, "2024") for s, n in (
        ("NVDA", "NVIDIA"), ("AVGO", "Broadcom"), ("AMD", "AMD"), ("MRVL", "Marvell Technology"),
        ("ARM", "Arm Holdings"), ("MU", "Micron Technology"), ("CDNS", "Cadence Design Systems"))]
    members = tuple(R.ThemeMember(company=co(s, n), top_segment=g, top_segment_share=sh, fiscal_year=fy)
                    for s, n, g, sh, fy in rows) + (
        R.ThemeMember(company=co("INTC", "Intel"), top_segment="Client Computing Group", top_segment_share=0.55,
                      fiscal_year="2025", latest_fiscal_year="2026"),)
    rec = R.ThemeExplainer(series="theme_explainer", slug="silicon-rush", title="Silicon Rush", members=members,
                           tickers_as_of=date(2026, 2, 20), theme_size=8, angle="make_money", posted_on=run)
    out = compose(rec, run)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["Intel"] == "Client Computing, FY2025 · 55% of revenue"
    assert out["image_spec"]["subtitle"].endswith("fiscal 2024")


def test_p4_a_walked_back_year_never_sets_the_sub_lines_years_even_on_a_record_built_elsewhere():
    """The template's own guard behind the adapter's (which never walks from a newest year after the run): a stored
    record whose walked-back fact is of the RUN's year (its newest one after it) beside current facts two years older
    — the sub line is still the current facts' ("fiscal 2025"), they stay untagged, and the walked fact is tagged.
    Were the walked year to set the sub line, the current facts would all be tagged and the post refused."""
    run = date(2027, 3, 3)
    rows = [(s, n, "Data Center", 0.5, "2025") for s, n in (
        ("NVDA", "NVIDIA"), ("AVGO", "Broadcom"), ("AMD", "AMD"), ("MRVL", "Marvell Technology"),
        ("ARM", "Arm Holdings"), ("MU", "Micron Technology"), ("CDNS", "Cadence Design Systems"))]
    members = tuple(R.ThemeMember(company=co(s, n), top_segment=g, top_segment_share=sh, fiscal_year=fy)
                    for s, n, g, sh, fy in rows) + (
        R.ThemeMember(company=co("INTC", "Intel"), top_segment="Client Computing Group", top_segment_share=0.55,
                      fiscal_year="2027", latest_fiscal_year="2028"),)
    rec = R.ThemeExplainer(series="theme_explainer", slug="silicon-rush", title="Silicon Rush", members=members,
                           tickers_as_of=date(2027, 2, 20), theme_size=8, angle="make_money", posted_on=run)
    out = compose(rec, run)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert out["image_spec"]["subtitle"].endswith("fiscal 2025")
    assert tiles["NVIDIA"] == "Data Center · 50% of revenue" and tiles["Intel"].startswith("Client Computing, FY2027")


# ── 2026-11-20 content round: the Money Map cover, the Form 4 roundup cover, the theme tiles' case and labels ───────


@pytest.mark.parametrize("factory, figure, label", [
    (costco, "$2.94", "of every $100 of revenue was net income"),
    (rivian, "$66.38", "net loss for every $100 of revenue"),
])
def test_r5_the_money_map_cover_reads_like_the_picture(factory, figure, label):
    """The final build's Costco cover read backwards ("OF EVERY $100 OF COSTCO REVENUE WAS NET INCOME" over "$2.94").
    The cover now reads like the picture: its headline IS the picture's ("How Costco makes money", two-tone at the
    worker: "makes money" is a gold tail), its chip the picture plate's TICKER, then the "per $100" figure with its
    label under it — the picture callout's words — and every string is one the server lets the worker draw, in that
    order (kicker, chip, figure, label, headline). Mutation-checked by hand (2026-11-20): the old opening card (the
    "of every $100 of {co} revenue …" headline, the "Fiscal 2025" chip) turned every assertion red."""
    out = compose(factory(), THU)
    card, spec = out["opening_card"], out["image_spec"]
    assert card["headline"] == spec["title"] == T._t("mm.img.title", co=factory().company.name)
    assert card["chip"] == spec["header"]["chip"] == factory().company.symbol
    assert (card["figure"], card["figure_label"]) == (figure, label)
    # the picture callout's own words: "For every $100 of revenue, {figure} was net income" / "…, the net loss was …"
    assert figure in spec["callout"] and "every $100 of revenue" in label and "every $100 of revenue" in spec["callout"]
    assert ("net income" in label) == ("net income" in spec["callout"])
    strings = onscreen.opening_strings(card, _logo_entries(out))
    assert [s for s in strings if s in (card["chip"], figure, label, card["headline"])] == [
        card["chip"], figure, label, card["headline"]]
    assert onscreen.validate_opening_card(card, {r["key"] for r in out["logo_refs"]}) is None
    assert T.validate_package(out, record=factory()) == []
    # the narration still opens on the cover's figure (the hook says the same "per $100" figure)
    assert figure in out["hook"]


def test_r5_a_figure_label_is_bounded_and_only_ever_explains_a_figure():
    """The server's opening-card walker (and the worker's, pinned equal): `figure_label` is optional, a drawable string
    of at most OPENING_FIGURE_LABEL_MAX_CHARS, and refused without a figure (a label standing alone would explain
    nothing). Mutation-checked by hand (2026-11-20): with the "missing figure" check removed this turned red."""
    out = compose(costco(), THU)
    keys = {r["key"] for r in out["logo_refs"]}
    card = out["opening_card"]
    assert onscreen.OPENING_FIGURE_LABEL_MAX_CHARS == 60 and len(card["figure_label"]) <= 60
    bare = {k: v for k, v in card.items() if k != "figure"}
    assert "figure" in (onscreen.validate_opening_card(bare, keys) or "")
    long = dict(card, figure_label="x" * 61)
    assert "label_too_long" in (onscreen.validate_opening_card(long, keys) or "")
    for bad in (7, "", "a\nb", "https://caydex.example"):
        assert onscreen.validate_opening_card(dict(card, figure_label=bad), keys) is not None, bad
    assert "figure_label" in onscreen.OPENING_KEYS[1]


@pytest.mark.parametrize("factory", [ceo_roundup, insider_mixed])
def test_r5_a_form4_roundup_cover_is_its_pictures_headline_and_list(factory):
    """RESTYLE_SPEC §1 (the 13F cover's idea, cover_13f_berkshire.jpg, on every template video): the roundup's cover
    showed the lead company alone under "AT LEAST 3 CEOs …". Its opening card is now the picture's title and the
    picture's first INS_COVER_ROWS rows (logo, ticker chip, name, role, amount) — no plate, no chip, no figure —
    every string allowed by the server, no person's name anywhere on it. Its first row is the lead the hook narrates.
    Mutation-checked by hand (2026-11-20): the old plain opening card turned every assertion red."""
    rec = factory()
    out = compose(rec, MON)
    card, spec = out["opening_card"], out["image_spec"]
    rows = spec["sections"][0]["rows"]
    assert card["headline"] == spec["title"]
    # owner decisions 2026-11-20 ("All rows, compact"): every row the picture draws (up to five)
    assert card["list"] == {"rows": rows[:T.INS_COVER_ROWS]} and T.INS_COVER_ROWS == 5 and len(rows) == 3
    assert card["logos"] == [rows[0]["logo"]] and not {"chip", "figure", "figure_label"} & set(card)
    assert onscreen.validate_opening_card(card, {r["key"] for r in out["logo_refs"]}) is None
    strings = onscreen.opening_strings(card, _logo_entries(out))
    assert card["headline"] in strings and all(c in strings for r in card["list"]["rows"] for c in r["cells"])
    for person in {r.person_name for r in rec.rows if r.person_name}:
        assert not any(person.split()[-1] in s for s in strings), person
    # the narration's first line is about the cover's first row: its company and its amount
    lead = rec.rows[0]
    assert rows[0]["cells"][0] == lead.company.name and rows[0]["cells"][2] == T.money_image(lead.amount_usd)
    assert T.money_words(lead.amount_usd) in out["hook"] and T.spoken_company(lead.company.name) in out["hook"]
    assert T.validate_package(out, record=rec) == []


_FIRST_INT = re.compile(r"\d+")


def _count_in(text):
    """The first whole number in `text` (every Form 4 headline, hook and caption first line opens with its count)."""
    m = _FIRST_INT.search(text or "")
    return int(m.group()) if m else None


#: An eighth company for the list builders below (SAMPLE figures; its total is the smallest). Since the final review of
#: 2026-11-26 a record holds at most five, so the 3..INSIDER_MAX_ROWS sweeps take the first five.
_ISP8_EXTRA = ("PEP", "PepsiCo", "director", 150_000.0, 1_000.0, (149.80, 150.20))
_ROUNDUP_EXTRA = buy("CTSO", "Contoso", "ceo", None, 120_000.0, 2_400.0, 1, [date(2026, 11, 14)])


def _isp_rows(n):
    rows = list(_ISP7) + [_ISP8_EXTRA]
    day = date(2026, 11, 18)
    return isp_list(*[isp_company(ip(sym, name, role, None, amt, shares, 2, [day], [day], cost))
                      for sym, name, role, amt, shares, cost in rows[:n]],
                    start=date(2026, 11, 17), end=day)


@pytest.mark.parametrize("n", range(R.INSIDER_MIN_ROWS, R.INSIDER_MAX_ROWS + 1))
def test_review_1126_a_form4_post_shows_one_count_everywhere(n):
    """Review of 2026-11-26 (fidelity): ONE count everywhere — the picture title, the cover headline, the hook (the
    insider list) or the narration's "At least {m} more" + 1 (the roundup), and every caption's first line all say n.
    (That review found a 6-8 company post drawing "5" over captions saying 6-8; the final review then held every Form
    4 record to the owner's "at most 5", so n runs 3..5 and every company is drawn.)"""
    # the insider LIST (spotlight lane)
    rec = _isp_rows(n)
    out = compose(rec, SPOT_THU)
    assert out["image_spec"]["title"] == out["opening_card"]["headline"]
    assert _count_in(out["image_spec"]["title"]) == _count_in(out["hook"]) == n
    assert _count_in(out["image_post"]["title"]) == n
    for f, body in out["captions"].items():
        if f != "youtube_title":
            assert _count_in(body.split("\n", 1)[0]) == n, (f, body)
    assert _count_in(out["captions"]["youtube_title"]) == n
    assert ("more" in out["image_spec"]["sections"][0]) is (n > T.INS_PICTURE_ROWS)
    assert T.validate_package(out, record=rec) == []
    # the weekly ROUNDUP (main lane): the first n of the sample CEOs
    rows = [*CEO_ROWS7, _ROUNDUP_EXTRA][:n]
    week_rec = week(*rows)
    out = compose(week_rec, MON)
    title = out["image_spec"]["title"]
    assert title == out["opening_card"]["headline"] == out["image_post"]["title"]
    assert _count_in(title) == n
    assert _count_in(out["video_script"][-1]) + 1 == n                     # "At least {m} more chief executives …"
    for f in ("x", "bluesky", "threads", "facebook", "linkedin"):
        body = out["captions"].get(f)
        if body is not None:
            assert body.startswith(title), (f, body)                        # the same count AND the same noun
    assert T.validate_package(out, record=week_rec) == []


def test_review_1126_a_roundup_headline_names_the_role_of_every_row():
    """The role noun of the roundup's headline groups EVERY row, as its captions do: four directors above a CFO read
    "At least 5 company insiders …", never "At least 5 directors …" (a CFO is no director)."""
    dirs = [buy(sym, name, "director", None, amt, 1_000.0, 1, [date(2026, 11, 13)])
            for sym, name, amt in (("NKE", "Nike", 900_000.0), ("HSY", "Hershey", 800_000.0),
                                   ("DPZ", "Domino's Pizza", 700_000.0), ("KO", "Coca-Cola", 600_000.0),
                                   ("PEP", "PepsiCo", 500_000.0), ("STZ", "Constellation Brands", 400_000.0))]
    cfo = buy("LTW", "Litware", "cfo", None, 120_000.0, 3_000.0, 1, [date(2026, 11, 14)])
    rec = week(*dirs[:4], cfo, series="insider_buys")
    out = compose(rec, MON)
    title = out["image_spec"]["title"]
    assert title.startswith("At least 5 company insiders "), title
    assert [r["cells"][1] for r in out["image_spec"]["sections"][0]["rows"]] == ["Director"] * 4 + ["CFO"]
    for f in ("x", "bluesky", "facebook", "linkedin"):
        body = out["captions"].get(f)
        if body is not None:
            assert body.startswith(title), (f, body)
    assert T.validate_package(out, record=rec) == []


def test_r5_a_five_row_roundup_moves_its_note_to_the_caption():
    """form4_roundup5.jpg (the designers' notes: "With the weekly roundup's approved chip-above row it does not fit at
    all"): a FIVE-row picture draws no note panel — its note (held indirectly, an amendment) MOVES to the caption, where
    every caption riding the picture must keep it (else posted as text / dropped); four rows keep the panel. The cover
    never carried the note."""
    five = compose(ceo_roundup5(), MON)
    assert "notes" not in five["image_spec"] and "notes" not in five["opening_card"]
    note = "Some of the shares are reported as held indirectly. The figures include an amended Form 4 (Form 4/A)."
    for f in ("facebook", "linkedin"):
        assert note in five["captions"][f], f
    for f in ("x", "bluesky", "threads"):
        body = five["captions"].get(f)
        assert body is None or f in five.get("text_outlets", []) or note in body, (f, body)
    assert not any("held indirectly" in p for p in five["image_post"]["paragraphs"])     # the alt text: the picture
    four = compose(week(GME, LOW, INTC, SBUX), MON)
    assert four["image_spec"]["notes"] == ["Some of the shares in these purchases are reported as held indirectly.",
                                           "The figures include an amended Form 4 (Form 4/A)."]
    assert T.INS_ROUNDUP_NOTE_MAX_ROWS == 4


def test_r5_there_is_no_one_or_two_company_form4_post():
    """Owner decisions 2026-11-20: a week of one or two companies is no record (the adapter skips it,
    ``ceo_too_few_companies``), and a forged one is refused by the composer (`too_few_rows`)."""
    for rows in ((GME,), (GME, LOW)):
        with pytest.raises(ValueError):
            week(*rows, pad=False)
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(_forged(ceo_roundup(), "rows", rows), MON)
        assert e.value.code == "too_few_rows"


@pytest.mark.parametrize("filed, drawn", [
    ("Data center", "Data Center"), ("Semiconductor solutions", "Semiconductor Solutions"),
    ("Food and beverage", "Food and Beverage"), ("the Americas", "The Americas"),
    ("third-party seller services", "Third-Party Seller Services"), ("QCT", "QCT"), ("iPhone", "iPhone"),
    ("Office of the CFO", "Office of the CFO"), ("Products for the home", "Products for the Home"),
    ("Data Center", "Data Center"), ("U.S.", "U.S."), ("e-commerce", "E-Commerce"), ("5g networks", "5G Networks"),
    ("Internet  of things", "Internet of Things"),
])
def test_r5_a_segment_name_is_drawn_in_title_case_only_where_the_feed_wrote_it_all_lower(filed, drawn):
    """The final build drew Marvell's "Data center" beside NVIDIA's "Data Center". A word written ENTIRELY in lower case
    is capitalised (each hyphen part), except a small word after the first; an all-caps acronym, a mixed-case brand and
    a capitalised word stay as filed."""
    assert T.segment_title_case(filed) == drawn


def test_r5_the_theme_tiles_and_their_words_share_one_case():
    """Every place the theme post says a segment — the tile, the cover's rows, the narration, the captions — says it in
    ONE case; the drawn strings are the server's own (allowed), never the feed's lower case. Mutation-checked by hand
    (2026-11-20): without `segment_title_case` in the fact list the tile read "Data center · 74% of revenue"."""
    rows = [r if r[0] != "MRVL" else ("MRVL", "Marvell Technology", "Data center", 0.74, "2026") for r in AI_CHIPS]
    rows = [r if r[0] != "AVGO" else ("AVGO", "Broadcom", "Semiconductor solutions", 0.58, "2025") for r in rows]
    rec = theme(rows, theme_size=14)
    out = compose(rec, THEME_RUN)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["Marvell Technology"] == "Data Center · 74% of revenue"
    assert tiles["Broadcom"].startswith("Semiconductor Solutions")
    text = json.dumps(out)
    assert "Data center" not in text and "Semiconductor solutions" not in text
    allowed = onscreen.image_strings(out["image_spec"], _logo_entries(out))
    assert "Data Center · 74% of revenue" in allowed
    assert T.validate_package(out, record=rec) == []


def test_r5_klas_filed_segment_draws_its_short_label_and_keeps_its_share():
    """The final build's KLA tile drew "Semiconductor Process Control" without "· 91% of revenue": the server counted the
    label as two lines (text_metrics' flat half-pixel allowance said 358 px; Pillow draws 344 in the 347 px column).
    Both are fixed: the code-owned short label "Process Control" (KLA's own business name), and the tighter bound —
    so the FILED name alone also keeps its share now. The captions keep the filed name."""
    assert T.THEME_SEGMENT_SHORT_LABELS["Semiconductor Process Control"] == "Process Control"
    assert T.text_metrics.line_count("Semiconductor Process Control", T.THEME_TILE_FLOOR_PX, T.THEME_TILE_TEXT_W) == 1
    assert T._tile_line(1, "Semiconductor Process Control", "91%") == (
        "Semiconductor Process Control · 91% of revenue")
    rows = [r if r[0] != "TXN" else ("KLAC", "KLA", "Semiconductor Process Control", 0.91, "2025")
            for r in AI_CHIPS if r[0] != "KLAC"]
    out = compose(theme(rows, theme_size=13), THEME_RUN)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["KLA"] == "Process Control · 91% of revenue"
    assert "KLA: Semiconductor Process Control, 91% of fiscal 2025 revenue." in out["captions"]["linkedin"]


def test_final_coherents_filed_segment_draws_its_short_label_and_keeps_its_share():
    """Final review (2026-11-20): Coherent's short label had no test — deleting it kept the suite green, while the
    filed "Datacenter and Communications" alone draws WITHOUT its "· 62% of revenue" (the KLA defect). The tile now
    reads its short label with its share; the captions keep the filed name."""
    assert T._tile_line(1, "Datacenter and Communications", "62%") == "Datacenter and Communications"
    rows = [r if r[0] != "TXN" else ("COHR", "Coherent", "Datacenter and Communications", 0.62, "2025")
            for r in AI_CHIPS]
    out = compose(theme(rows, theme_size=14), THEME_RUN)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["Coherent"] == "Datacenter & Communications · 62% of revenue"
    assert "Coherent: Datacenter and Communications, 62% of fiscal 2025 revenue." in out["captions"]["linkedin"]
    allowed = onscreen.image_strings(out["image_spec"], _logo_entries(out))
    assert "Datacenter & Communications · 62% of revenue" in allowed


#: The short labels added in the final build (2026-11-20), pinned as a LITERAL list: a test parametrized over the table
#: itself loses a deleted entry's case with the entry.
_FINAL_SHORT_LABELS = [("Semiconductor Process Control", "Process Control"),
                       ("Datacenter and Communications", "Datacenter & Communications")]


@pytest.mark.parametrize("filed, short", _FINAL_SHORT_LABELS)
def test_final_the_final_builds_short_labels_stay_listed(filed, short):
    assert T.THEME_SEGMENT_SHORT_LABELS.get(filed) == short


@pytest.mark.parametrize("filed", sorted(T.THEME_SEGMENT_SHORT_LABELS))
def test_final_every_short_label_key_is_a_name_a_theme_member_can_carry(filed):
    """A key a theme member can never carry is dead code that reads as a rule: McDonald's 58-character "International
    Developmental Licensed Markets and Corporate" was listed while `ThemeMember.top_segment` refuses anything over 40
    characters (THEME_MEMBER_SEGMENT_MAX_CHARS) and the tile looks the name up in `segment_title_case`'s form."""
    short = T.THEME_SEGMENT_SHORT_LABELS[filed]
    assert len(filed) <= T.THEME_MEMBER_SEGMENT_MAX_CHARS
    assert T.segment_title_case(filed) == filed
    assert T.text_metrics.line_count(short, T.THEME_TILE_FLOOR_PX, T.THEME_TILE_TEXT_W) == 1
    assert T._tile_line(1, short, "91%") == f"{short} · 91% of revenue"


# ── visual-fidelity round 2 (2026-11-20) ────────────────────────────────────────────────────────────────────────────

#: An ASCII hyphen used as a minus sign: before a "$" or a digit, not inside a word ("8-K", "10b5-1") or a range.
_ASCII_MINUS = re.compile(r"(?<![\w.–])-(?=\$|[0-9])")


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_fidelity2_no_drawn_negative_figure_carries_an_ascii_hyphen(name, factory, run_date):
    """A Money Map's eliminations drew "-$4.3B" and an earnings video card "EPS -$0.05" — while every other drawn
    negative figure (the earnings table, a theme's growth and profit, a 13F change) carries U+2212. Every string a
    template may DRAW — its picture, its cover, its video cards — now does; a DOLLAR figure in the captions and the alt
    text keeps "-$0.05" / "-$4.3B" (plain text), as before (the next test pins the Money Map's alt text). A theme
    short caption's growth percent is the picture's own form ("revenue −2%", `growth_image`), on purpose."""
    out = compose(factory(), run_date)
    drawn = list(onscreen.opening_strings(out["opening_card"], out["logo_refs"]))
    drawn += [c[k] for c in out["cards"] for k in ("title", "body") if c.get(k)]
    if out.get("image_spec") is not None:
        drawn += onscreen.image_strings(out["image_spec"], out["logo_refs"])
    assert not [s for s in drawn if _ASCII_MINUS.search(s)], name


def test_fidelity2_the_image_money_form_signs_with_u2212_and_the_words_keep_the_hyphen():
    assert T.money_image(-4_300_000_000) == "−$4.3B" and T.money_image(-850) == "−$850"
    assert T.money_words(-4_300_000_000) == "-$4.3 billion"
    out = compose(comcast(), THU)
    (elim,) = [b for b in out["image_spec"]["segments"] if b["style"] == "outline"]
    assert elim["value"] == "−$4.3B" and _ASCII_MINUS.search(elim["value"]) is None
    # final review (2026-11-20): the alt text shares the bars' values, and the drawn U+2212 leaked into it
    # ("Intersegment eliminations −$4.3B"); the alt text is plain text, so it keeps the hyphen, as the earnings alt
    # text does ("EPS -$0.05")
    alt = json.dumps(out["image_post"], ensure_ascii=False)
    assert "Intersegment eliminations -$4.3B" in alt and "\u2212" not in alt
    assert out["image_post"]["paragraphs"][0] == (
        "Bars show how revenue broke down: Connectivity and Platforms $82B; Content and Experiences $43.4B; "
        "Everything else $2.6B; Intersegment eliminations -$4.3B.")
    assert T._alt_money("−$4.3B") == "-$4.3B" and T._alt_money("$82B") == "$82B"


# ── owner decision 2026-11-20: the Money Map LOSS year's costs card ("A: costs vs revenue") ─────────────────────────
#
# The owner's note: "for companies who are loss, it's a bit hard to understand … a cost or loss will have a longer red
# bar than the revenue, so users will know, all the total loss or cost will minus the revenue, so it will be the net
# loss", and his choice "A: costs vs revenue (Recommended)" (money_map_loss_A_rivian.jpg / money_map_loss_A_mixed.jpg /
# money_map_loss_A_rivian_cover.jpg, the designers' production notes). A loss year's picture and cover draw the COSTS
# card; a profit year is untouched (its goldens above pin it).


def coherent(**kw):
    """The mock's mixed year (money_map_loss_A_mixed.jpg — Coherent-shaped SAMPLE figures): a gross PROFIT, an operating
    loss, and interest, tax and other items that were a COST ($100M), so a net loss of $700M."""
    base = dict(series="money_map", company=co("COHR", "Coherent"), fiscal_year="2025", period_end=date(2025, 6, 30),
                segments=(R.Segment("Networking", 2_000_000_000.0), R.Segment("Lasers", 1_300_000_000.0),
                          R.Segment("Materials", 700_000_000.0)),
                other_usd=None, eliminations_usd=None, revenue_usd=4_000_000_000.0, gross_profit_usd=1_400_000_000.0,
                operating_profit_usd=-600_000_000.0, net_income_usd=-700_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


def _costs(rec):
    return compose(rec, THU)["image_spec"]["costs"]


_UNIT = {"T": 10 ** 12, "B": 10 ** 9, "M": 10 ** 6, "K": 10 ** 3}


def _fig(text):
    """A drawn costs-card figure back to dollars ("$5.9B" → 5.9e9, "+$110M", "$850")."""
    body = text.lstrip("+$").replace(",", "")
    return Decimal(body.rstrip("TBMK")) * _UNIT.get(body[-1:], 1)


def _half(text):
    """Half the last shown digit of a drawn figure ("$5.9B" → $0.05B, "$5.88B" → $0.005B, "$850" → $0.5)."""
    body = text.lstrip("+$").replace(",", "")
    digits = body.rstrip("TBMK")
    dec = len(digits.split(".")[1]) if "." in digits else 0
    return Decimal("0.5") * Decimal(10) ** (-dec) * _UNIT.get(body[-1:], 1)


def test_loss_the_costs_card_derives_every_part_from_the_statement_lines():
    """Cost of sales = revenue − gross profit, operating expenses = gross profit − operating income, interest, tax and
    other items = net income − operating income, costs = revenue − net income — Rivian's mock figures (revenue $5.80B,
    gross −$80M, operating −$3.96B, net −$3.85B): $5.88B, $3.88B, +$110M, $9.65B, drawn at one decimal exactly as the
    approved mock ("$5.9B", "$3.9B", "+$110M", "$9.7B", "Net loss $3.9B", "$166.38" / "$66.38")."""
    c = _costs(rivian())
    assert c["revenue"] == {"label": "Revenue", "value": "$5.8B", "ratio": c["revenue"]["ratio"]}
    assert c["net"] == {"label": "Net loss", "value": "$3.9B"}
    assert (c["total"]["label"], c["total"]["value"]) == ("Costs and expenses", "$9.7B")
    assert [(p["label"], p["value"]) for p in c["parts"]] == [("Cost of sales", "$5.9B"),
                                                              ("Operating expenses", "$3.9B")]
    assert c["note"] == "+$110M from interest, tax and other items made the loss smaller"
    assert c["callout"] == ["For every $100 of revenue, costs were $166.38,", "so the net loss was $66.38"]
    # the ONE scale: other income → the bar is cost of sales + operating expenses = revenue − operating income ($9.76B)
    scale = Decimal("9.76e9")
    assert c["parts"][0]["ratio"] == float(T._q(Decimal("5.88e9") / scale, 4))
    assert c["parts"][1]["ratio"] == float(T._q(Decimal("3.88e9") / scale, 4))
    assert c["total"]["ratio"] == float(T._q(Decimal("9.65e9") / scale, 4))       # where the SOLID red ends
    assert c["revenue"]["ratio"] == float(T._q(Decimal("5.8e9") / scale, 4))
    # a gross LOSS: cost of sales runs past the revenue marker (a fact the picture shows)
    assert c["parts"][0]["ratio"] > c["revenue"]["ratio"]


@pytest.mark.parametrize("factory, parts, note, third", [
    # other INCOME (+$110M): two parts, the note, no third part — the hatched end
    (rivian, ["Cost of sales", "Operating expenses"], True, None),
    # other a COST (−$100M): a third, maroon part with its own legend entry, no note
    (coherent, ["Cost of sales", "Operating expenses", "Interest, tax and other items"], False, "$100M"),
    # exactly ZERO: omitted — two parts, no note
    (lambda: rivian(net_income_usd=-3_960_000_000.0), ["Cost of sales", "Operating expenses"], False, None),
    # too small to show (+$10M, under half the costs figure's last digit, $50M at one decimal): omitted too
    (lambda: rivian(net_income_usd=-3_950_000_000.0), ["Cost of sales", "Operating expenses"], False, None),
], ids=["other_income", "other_cost", "other_zero", "other_too_small"])
def test_loss_every_sign_of_interest_tax_and_other_items(factory, parts, note, third):
    c = _costs(factory())
    assert [p["label"] for p in c["parts"]] == parts
    assert ("note" in c) == note
    if third is not None:
        assert c["parts"][2]["value"] == third
        # a cost: every part positive and they add up — the bar's parts ARE the costs (the scale)
        assert c["total"]["ratio"] == 1.0
    if not note:
        # no hatched end: the parts span the whole bar, the solid red is the whole bar
        assert abs(sum(p["ratio"] for p in c["parts"]) - 1.0) <= 0.0002 and c["total"]["ratio"] == 1.0
    else:
        assert c["total"]["ratio"] < 1.0 and abs(sum(p["ratio"] for p in c["parts"]) - 1.0) <= 0.0002


def test_loss_an_omitted_sliver_of_other_is_absorbed_by_operating_expenses_never_by_cost_of_sales():
    """+$10M of other income (too small to show): cost of sales keeps its own ratio (its end against the revenue marker is
    a fact), operating expenses' BAR takes the difference so the bar ends where costs end — its drawn VALUE stays the
    statement's $3.88B ("$3.9B")."""
    rec = rivian(net_income_usd=-3_950_000_000.0)
    c = _costs(rec)
    costs = Decimal("5.8e9") + Decimal("3.95e9")
    assert c["parts"][0]["ratio"] == float(T._q(Decimal("5.88e9") / costs, 4))
    assert c["parts"][1]["ratio"] == float(T._q((costs - Decimal("5.88e9")) / costs, 4))
    assert c["parts"][1]["value"] == "$3.9B"


def test_loss_the_figures_go_to_two_decimals_only_when_one_decimal_does_not_add_up():
    """The designers' rounding rule: format, check the reader's arithmetic, else two decimals for that post. Revenue
    $5.84B, costs $9.66B, net loss $3.82B: at one decimal "9.7 − 5.8" is 3.9, not the "$3.8B" drawn — so every figure in
    the costs' unit takes two decimals ("$9.66B − $5.84B = $3.82B"); a figure in a smaller unit keeps its own form."""
    rec = rivian(revenue_usd=5_840_000_000.0, gross_profit_usd=-40_000_000.0, operating_profit_usd=-3_900_000_000.0,
                 net_income_usd=-3_820_000_000.0, segments=(R.Segment("Automotive", 4_660_000_000.0),
                                                            R.Segment("Software and services", 1_180_000_000.0)))
    plan = T._money_map_costs(Decimal("5.84e9"), Decimal("-0.04e9"), Decimal("-3.9e9"), Decimal("-3.82e9"))
    assert plan.places == 2 and plan.adds_up
    c = _costs(rec)
    assert (c["revenue"]["value"], c["total"]["value"], c["net"]["value"]) == ("$5.84B", "$9.66B", "$3.82B")
    assert [p["value"] for p in c["parts"]] == ["$5.88B", "$3.86B"]
    assert c["note"].startswith("+$80M ")
    # Rivian's own figures add up at one decimal: one decimal
    assert T._money_map_costs(Decimal("5.8e9"), Decimal("-0.08e9"), Decimal("-3.96e9"), Decimal("-3.85e9")).places == 1
    # one decimal is money_image's form, figure for figure
    for v in (850, 410_000, 74_440_000, 999_960_000, 1_000_000_000, 5_880_000_000, 47_960_000_000):
        assert T._costs_figure(Decimal(v), 1, "B")[0] == T.money_image(v)


def _two_decimal_loss():
    return rivian(revenue_usd=5_840_000_000.0, gross_profit_usd=-40_000_000.0, operating_profit_usd=-3_900_000_000.0,
                  net_income_usd=-3_820_000_000.0, segments=(R.Segment("Automotive", 4_660_000_000.0),
                                                             R.Segment("Software and services", 1_180_000_000.0)))


@pytest.mark.parametrize("make", [rivian, coherent, _two_decimal_loss,
                                  lambda: rivian(gross_profit_usd=None),
                                  lambda: rivian(net_income_usd=-5_800_000_000.0, operating_profit_usd=-5_800_000_000.0),
                                  lambda: coherent(revenue_usd=10_000_000_000.0, gross_profit_usd=4_000_000_000.0,
                                                   operating_profit_usd=1_000_000_000.0,
                                                   net_income_usd=-2_000_000_000.0,
                                                   segments=(R.Segment("Networking", 5_000_000_000.0),
                                                             R.Segment("Lasers", 3_250_000_000.0),
                                                             R.Segment("Materials", 1_750_000_000.0)))],
                         ids=["rivian", "coherent", "two_decimals", "no_gross", "loss_equals_revenue", "interest_heavy"])
def test_review_1126_a_loss_picture_prints_revenue_once(make):
    """Review of 2026-11-26 (fidelity): a loss year whose costs card needed two decimals drew "Revenue $5.8B" on the top
    card (`flow[0]`) and "Revenue $5.84B" on the costs card of the SAME picture. The top card now prints the costs card's
    revenue figure — the same string on both — on every loss year (at one decimal the two were already equal).
    Mutation-checked: without the override the two_decimals case fails."""
    rec = make()
    out = compose(rec, THU)
    spec = out["image_spec"]
    assert spec["flow"][0]["value"] == spec["costs"]["revenue"]["value"] == out["opening_card"]["costs"]["revenue"][
        "value"]
    assert T.validate_package(out, record=rec) == []
    if make is _two_decimal_loss:
        assert spec["flow"][0]["value"] == "$5.84B"
        # the alt text says the same figure the picture draws
        assert "revenue, $5.84B," in " ".join(out["image_post"]["paragraphs"])


def test_review_1126_the_older_pictures_alt_text_is_scanned_validated_and_compared_like_the_alt_text():
    """`image_post_pre_v23` is public text (the publisher may send it): every scan of the alt text reads it too — a
    banned verb in it is a violation, a malformed one is refused, one on a series whose older picture
    needs none is refused, and `revalidate` compares it with the recomposition (the person scans read it too:
    `_restricted_strings`). Mutation-checked: scanning `image_post` alone lets the banned verb through."""
    rec = rivian()
    out = compose(rec, THU)
    pre = out["image_post_pre_v23"]
    assert pre["title"] == out["image_post"]["title"] and pre["paragraphs"][-1] == out["image_post"]["paragraphs"][-1]
    assert "Gross loss" in pre["paragraphs"][1] and "costs" not in " ".join(pre["paragraphs"])         # the flow card
    assert T.validate_package(out, record=rec) == []
    assert T.revalidate(out, fact_sheet=sheet(rec), run_date=THU) == []

    def fields(o):
        return {(x["field"].split(".")[0].split("[")[0], x["code"]) for x in T.validate_package(o, record=rec)}

    bad = copy.deepcopy(out)
    bad["image_post_pre_v23"]["paragraphs"][0] = "Rivian beat the estimates and sold more trucks."
    assert any(f == "image_post_pre_v23" for f, _c in fields(bad)), fields(bad)
    assert any(x["field"] == "image_post_pre_v23" and x["code"] == "field_mismatch"
               for x in T.revalidate(bad, fact_sheet=sheet(rec), run_date=THU))
    for junk in ("text", {"title": pre["title"]}, {**pre, "extra": 1}, {**pre, "title": "Another title"}):
        odd = dict(out, image_post_pre_v23=junk)
        assert ("image_post_pre_v23", "image_post_invalid") in fields(odd), junk
    costco_out = compose(costco(), THU)
    assert "image_post_pre_v23" not in costco_out
    stray = dict(compose(ceo_roundup(), MON), image_post_pre_v23=pre)
    assert ("image_post_pre_v23", "image_post_invalid") in {
        (x["field"], x["code"]) for x in T.validate_package(stray, record=ceo_roundup())}


def test_review_1126_a_13f_list_past_five_rows_carries_the_five_row_alt_text():
    """A 13F list of eight (six drawn, "+2 more"): an older worker draws five rows and no "+N more" line, so its alt text
    says five of the changes and promises nothing in the caption; six rows or fewer drawn by both — no second alt text
    where the older picture is the same five (or fewer) rows."""
    out = compose(f13s_list8(), SPOT_SAT)
    pre, alt = out["image_post_pre_v23"], out["image_post"]
    assert alt["paragraphs"][0].startswith("It lists 6 of the filing's holding changes")
    assert pre["paragraphs"][0].startswith("It lists 5 of the filing's holding changes")
    assert any("listed in the caption" in p for p in alt["paragraphs"])
    assert not any("listed in the caption" in p for p in pre["paragraphs"])
    assert pre["paragraphs"][1].count(";") == 4 and alt["paragraphs"][1].count(";") == 5
    assert T.validate_package(out, record=f13s_list8()) == []
    for name, factory, day in _F13S_LIST_SAMPLES:
        rec = factory()
        drawn_rows = min(len(rec.moves), T.F13_PICTURE_ROWS)
        assert ("image_post_pre_v23" in compose(rec, day)) is (drawn_rows > 5), name


def test_review_1126_a_profit_year_keeps_its_one_decimal_revenue():
    out = compose(costco(), THU)
    assert out["image_spec"]["flow"][0]["value"] == T.money_image(costco().revenue_usd)
    assert "costs" not in out["image_spec"]


def test_loss_the_reader_check_catches_a_one_digit_miss_and_tolerates_a_finer_unit():
    nine_seven, five_eight = T._costs_figure(Decimal("9.66e9"), 1, "B"), T._costs_figure(Decimal("5.84e9"), 1, "B")
    assert not T._lands([(1, nine_seven), (-1, five_eight)], T._costs_figure(Decimal("3.82e9"), 1, "B"))
    assert T._lands([(1, nine_seven), (-1, five_eight)], T._costs_figure(Decimal("3.86e9"), 1, "B"))
    # "4.7 − 4 = 0.7" beside "$720M": one digit of the coarsest figure — lands
    assert T._lands([(1, T._costs_figure(Decimal("4.72e9"), 1, "B")), (-1, T._costs_figure(Decimal("4e9"), 1, "B"))],
                    T._costs_figure(Decimal("7.2e8"), 1, "B"))


def _random_loss(rnd):
    rev = Decimal(rnd.randint(50, 900_000)) * 10 ** 6
    ni = -(rev * Decimal(rnd.randint(1, 10_000)) / 10_000)                     # a net loss up to 100% of revenue
    shape = rnd.random()
    if shape < 0.15:
        gp, op = None, None
    elif shape < 0.25:
        gp, op = rev * Decimal(rnd.randint(-100, 99)) / 100, None
    else:
        gp = rev * Decimal(rnd.randint(-100, 99)) / 100
        op = max(-rev, gp - rev * Decimal(rnd.randint(1, 120)) / 100)
        if rnd.random() < 0.2:
            op = ni + rev * Decimal(rnd.randint(-30, 30)) / 1000                  # other items near zero, either sign
            op = min(max(op, -rev), gp)
    return rev, gp, op, ni.quantize(Decimal(1))


def test_loss_property_every_costs_card_is_exact_bounded_and_honest():
    """700 random loss years (seeded): every drawn figure is the true value rounded at its precision; the approved
    check_sum slack holds (every figure's half digit summed); one decimal is used only when the reader's arithmetic lands
    there; the callout's subtraction is exact (costs per $100 − $100 = the net loss per $100); the 2× bound holds (the
    revenue bar is at least half the scale, the costs bar the longer); every ratio is in [0, 1]."""
    rnd = random.Random(20261120)
    for _ in range(700):
        rev, gp, op, ni = _random_loss(rnd)
        plan = T._money_map_costs(rev, gp, op, ni)
        c = plan.block
        costs = rev - ni
        figures = [(c["revenue"]["value"], rev), (c["total"]["value"], costs), (c["net"]["value"], -ni)]
        if c["parts"]:
            figures += [(c["parts"][0]["value"], rev - gp), (c["parts"][1]["value"], gp - op)]
        if len(c["parts"]) == 3:
            figures.append((c["parts"][2]["value"], op - ni))
        if "note" in c:
            figures.append((c["note"].split(" ")[0], ni - op))
        for text, true in figures:
            fig = T._costs_figure(true, plan.places, T._scaled(costs, T._SHORT_UNITS, 2)[1])
            assert fig[0] == text.lstrip("+") and abs(fig[1] - true) <= fig[2], (text, true)
        # the approved slack (mock check_sum): parts ± other vs costs; costs − revenue vs net
        if c["parts"]:
            other = [c["note"].split(" ")[0]] if "note" in c else []
            signed = sum(_fig(p["value"]) for p in c["parts"]) - sum(_fig(t) for t in other)
            texts = [p["value"] for p in c["parts"]] + other + [c["total"]["value"]]
            assert abs(signed - _fig(c["total"]["value"])) <= sum(_half(t) for t in texts)
        texts = [c["total"]["value"], c["revenue"]["value"], c["net"]["value"]]
        assert abs(_fig(texts[0]) - _fig(texts[1]) - _fig(texts[2])) <= sum(_half(t) for t in texts)
        if plan.places == 1:
            assert plan.adds_up
        # the callout: one exact subtraction ("under $0.01" — a sub-cent loss — reads costs of "$100.00")
        lead, net = c["callout"]
        c100 = Decimal(lead.rsplit("$", 1)[1].rstrip(",").replace(",", ""))
        l100 = net[len("so the net loss was "):]
        assert c100 - 100 == (Decimal(0) if l100 == T.SUB_CENT else Decimal(l100.lstrip("$").replace(",", "")))
        # the 2x bound and the ratios
        ratios = [c["revenue"]["ratio"], c["total"]["ratio"], *(p["ratio"] for p in c["parts"])]
        assert all(0.0 <= r <= 1.0 for r in ratios)
        assert c["revenue"]["ratio"] >= 0.4999 and c["revenue"]["ratio"] <= c["total"]["ratio"]


def test_loss_the_costs_bar_is_at_most_twice_revenue_and_a_bigger_loss_is_refused():
    """The template's `implausible_figures` (the adapter's `money_map_loss_above_revenue`) bounds the layout: a net loss
    equal to revenue draws the revenue bar at exactly half the scale; one dollar more is refused."""
    c = _costs(rivian(net_income_usd=-5_800_000_000.0, operating_profit_usd=-5_800_000_000.0))
    assert c["revenue"]["ratio"] == 0.5 and c["callout"] == ["For every $100 of revenue, costs were $200.00,",
                                                           "so the net loss was $100.00"]
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rivian(net_income_usd=-5_800_000_001.0, operating_profit_usd=-5_800_000_000.0), THU)
    assert e.value.code == "implausible_figures"


@pytest.mark.parametrize("kw", [dict(gross_profit_usd=None), dict(operating_profit_usd=None),
                                dict(gross_profit_usd=None, operating_profit_usd=None)],
                         ids=["no_gross", "no_operating", "neither"])
def test_loss_without_both_statement_lines_the_costs_bar_is_one_undivided_piece(kw):
    out = compose(rivian(**kw), THU)
    c = out["image_spec"]["costs"]
    assert c["parts"] == [] and "note" not in c and c["total"] == {"label": "Costs and expenses", "value": "$9.7B",
                                                                  "ratio": 1.0}
    assert out["image_post"]["paragraphs"][1] == ("A second set of bars compares revenue, $5.8B, with costs and "
                                                  "expenses, $9.7B. The part of the costs bar past revenue is the net "
                                                  "loss, $3.9B.")
    assert T.validate_package(out, record=rivian(**kw)) == []


def test_loss_a_profit_year_carries_no_costs_card_anywhere():
    """A PROFIT year keeps today's approved card key for key (its goldens pin the opening card and the captions): no
    `costs` on the picture or the cover, no cover sub line, the flow alt text."""
    out = compose(costco(), THU)
    assert "costs" not in out["image_spec"] and not {"costs", "subtitle"} & set(out["opening_card"])
    assert out["image_post"]["paragraphs"][1].startswith("A second set of bars shows Revenue $275.2B; Gross profit")
    with pytest.raises(ValueError):
        T._money_map_costs(Decimal(1), None, None, Decimal(0))                 # a costs card is a loss year's only


@pytest.mark.parametrize("factory", [rivian, coherent, lambda: rivian(gross_profit_usd=None)],
                         ids=["rivian", "coherent", "no_gross"])
def test_loss_the_cover_is_the_costs_card_and_every_string_is_allowed_and_clean(factory):
    """The cover carries the picture's own costs card and sub line (money_map_loss_A_rivian_cover.jpg), keeping its
    figure and label (the fallback card); every new string is drawable, canonical and passes the package's checks —
    the picture's allow-list and the cover's hold them all, and no drawn string says "gross loss" / "operating loss"."""
    rec = factory()
    out = compose(rec, THU)
    assert T.validate_package(out, record=rec) == []
    op, spec = out["opening_card"], out["image_spec"]
    assert op["costs"] == spec["costs"] and op["subtitle"] == spec["subtitle"]
    assert {"figure", "figure_label"} <= set(op)
    keys = onscreen.logo_keys(out["logo_refs"])
    assert onscreen.validate_opening_card(op, keys) is None and onscreen.validate_image_spec(
        spec, keys, footer=out["image_footer"]) is None
    allowed_pic = set(onscreen.image_strings(spec, out["logo_refs"]))
    allowed_cover = set(onscreen.opening_strings(op, out["logo_refs"]))
    c = spec["costs"]
    drawn = ([c[k][f] for k in ("revenue", "net", "total") for f in ("label", "value")]
             + [p[f] for p in c["parts"] for f in ("label", "value")] + ([c["note"]] if "note" in c else [])
             + c["callout"])
    assert len(drawn) >= 8 and set(drawn) <= allowed_pic and set(drawn) <= allowed_cover
    assert spec["subtitle"] in allowed_cover
    for s in drawn:
        assert onscreen.drawable_problem(s) is None and clean(s) == s
        assert not re.search(r"(?i)\b(gross|operating) loss\b", s)
        assert not copy_rules.contains_banned_copy(s) and not copy_rules.contains_forecast(s)
        assert not T.NEWS_BANNED_RE.search(s) and not T.OFF_TABLE_VERBS_RE.search(s)


def test_loss_the_words_agree_with_the_picture():
    """Narration and captions are unchanged (owner: "unless it now contradicts the picture") — and they do not: the
    same net loss and per-$100 figure as the picture's callout; "gross loss" only when the picture's cost of sales runs
    past its revenue, "operating loss" only when its costs before other items do; the picture itself never says either
    word (the confusing steps are gone), nor does its alt text."""
    for rec in (rivian(), coherent()):
        out = compose(rec, THU)
        c = out["image_spec"]["costs"]
        words = " ".join(out["video_script"]) + " " + out["captions"]["linkedin"]
        per100 = c["callout"][1].rsplit(" ", 1)[1]
        assert per100 in out["hook"] and per100 in out["captions"]["linkedin"]
        rev, cos = _fig(c["revenue"]["value"]), _fig(c["parts"][0]["value"])
        cos_opex = cos + _fig(c["parts"][1]["value"])
        assert ("gross loss" in words) == (rec.gross_profit_usd < 0) == (cos > rev)
        assert ("operating loss" in words) == (rec.operating_profit_usd < 0) == (cos_opex > rev)
        alt = " ".join(out["image_post"]["paragraphs"])
        assert not re.search(r"(?i)\b(gross|operating) loss\b", alt)
        assert c["net"]["value"] in alt and c["total"]["value"] in alt
