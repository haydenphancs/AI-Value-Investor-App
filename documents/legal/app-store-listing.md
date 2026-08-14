# App Store Connect — listing copy (draft for review)

Paste into ASC → App Information / Pricing / the version's Product Page. Nothing here existed
before 2026-08-14; the launch checklist stated a *constraint* on this copy (§7's metadata rule) but
never assigned writing it.

**Every field below obeys three rules, and breaking any one of them is a rejection or a rewrite:**

1. **No real investor names** anywhere — name, subtitle, description, keywords, screenshots
   (Guideline 5.2.1). Describing a *methodology* in prose is fine; naming a feature after a living
   person is not. See the "Do not use" list at the bottom.
2. **No advice framing.** No "should buy", "top picks", "recommendations", "signals to act on",
   "beat the market", "guaranteed", or any implied return. The app is an information and education
   tool; §1 of the checklist identifies advice-framing as the single likeliest rejection hook.
3. **Nothing that isn't in the shipping build.** Every claim below maps to a real surface.

Character counts are measured and noted. Apple counts characters, not bytes.

---

## App Name (30 max)

```
Caydex
```
**6 / 30.** Matches `INFOPLIST_KEY_CFBundleDisplayName = Caydex` and the existing ASC record
(Apple ID 6759525689). Do not append a tagline here — the subtitle field is for that, and a
name-plus-tagline reads as keyword stuffing.

---

## Subtitle (30 max)

```
Research, not hype
```
**18 / 30.** This is the app's own line, from `OnboardingView.swift:139` — the first thing a new
user reads. Reusing it keeps the store page and the first run consistent, which is worth more than
squeezing in keywords the Keywords field already covers.

**Alternates if you want more search weight:**

| Option | Chars |
|---|---|
| `Stock research, explained` | 25 |
| `Company research in plain English` | 33 ❌ too long |
| `Company research, explained` | 27 |

---

## Keywords (100 max, comma-separated, no spaces after commas)

```
stock,research,investing,fundamentals,earnings,portfolio,watchlist,valuation,finance,13F,ETF,crypto
```
**100 / 100.** Notes on the choices:

- **Do not repeat the app name or the subtitle words** — Apple already indexes both. Every word
  here is absent from the name and subtitle, which is why "research" appears in Keywords despite
  being in the subtitle above; if you pick a subtitle alternate that keeps "research", drop it here
  and add `dividends` (+10).
- Singular only. Apple matches plurals automatically; `stocks` would waste 1 char.
- No competitor names, no real investor names, no "AI" (see the caution below).
- `13F` earns its place: it is the actual filing type behind the Tracking tab and a term people
  search deliberately.

⚠️ **"AI" is deliberately not a keyword.** It invites the reviewer to scrutinise the AI surfaces
first, and Apple's revised age-rating questionnaire now asks about AI-generated content directly.
The description discloses the AI features honestly — that is the right place for it, not a keyword
that buys little traffic and raises the review temperature.

---

## Promotional Text (170 max — optional, editable without a new build)

```
New: track congressional and institutional filings alongside your watchlist, and get a plain-English read on why a stock moved today.
```
**132 / 170.** This field is *not* reviewed as part of the binary, so it's the one place you can
update messaging between releases. Leave it out for 1.0 if you'd rather ship less surface area.

---

## Description (4000 max)

```
Caydex is a research tool for people who want to understand a company before they form an
opinion about it — not a feed of tips.

It reads the filings, the fundamentals and the news, then explains what it found in plain
English. You decide what to do with that.


WHAT'S INSIDE

Home
A dashboard built around what you actually follow: market movers, your watchlist, and the
day's activity across the companies and sectors you picked during setup.

Updates
A running feed of what changed and why. When a stock makes an unusual move, Caydex looks for
the catalyst — an earnings surprise, a filing, a news event — and writes up what it found
instead of just showing you a red number.

Research
Full company research reports covering fundamentals, growth, profitability, financial health,
valuation, moat, and the bull and bear case. Every figure is sourced from public financial
data and company filings, and every report is a point-in-time snapshot you can come back to.
You can read the same company through several different analytical lenses — a
quality-and-durability view, a growth view, a value view — so you can see where they agree
and where they don't.

Tracking
Institutional 13F holdings and congressional trading disclosures, with filing and disclosure
dates shown honestly. Follow the funds and filers you care about and see what changed quarter
to quarter.

Wiser
An education library: original study guides distilling the ideas of ten well-known investing
books, plus original lessons and articles on how markets and businesses actually work.
Everything is narrated, with read-along highlighting, and keeps playing when your screen is
locked.

Ask Cay AI
A conversational assistant that can answer questions about any company on screen, in as much
or as little depth as you want. It cites what it used.


ALSO IN THE APP

• Watchlists and portfolios, with a diversification score computed from holdings you enter
  yourself
• Price alerts, earnings reminders, and opt-in notifications for the companies you follow —
  all configurable, all off until you allow them
• Detail screens for stocks, ETFs, indices, crypto and commodities, with interactive charts
• Light and dark themes, Dynamic Type, and VoiceOver support


HOW IT'S PAID FOR

Caydex is free to browse. Market data, company detail screens, news, search and the education
library don't require an account.

AI-generated research reports and report chat cost credits, because each run has a real
per-use cost. A free account includes a monthly credit allowance. Pro and Max subscriptions
raise that allowance, and credit packs are available if you want more without subscribing.
Credits are only spent inside the app on AI generation; they are not a currency, cannot be
transferred, and cannot be cashed out.


IMPORTANT

Caydex is an information and education tool. It is not a broker-dealer, an investment adviser,
or a financial institution. It holds no funds, connects to no brokerage, and executes no
trades. Nothing in the app is financial advice or a recommendation to buy or sell any
security, and no outcome is promised or implied.

Company analysis, written reports and chat responses are generated by AI and are labelled as
such throughout the app. The app also shows several of its own computed indicators — a
technical meter, an estimated fair value, and a company score. These are outputs of published
formulas applied to public data, presented as information for your own research. Every one
carries a disclaimer in the app, and Caydex asks you to acknowledge this before showing you
any analysis.

Market and financial data may be delayed and may contain errors. Always verify against primary
sources before making any decision. Past performance says nothing about future results.

Terms of Use: https://caydexinvest.com/terms
Privacy Policy: https://caydexinvest.com/privacy
Support: https://caydexinvest.com/support
```

**3,906 / 4000.** Measured, not estimated — the first draft came in at 4,018 and would have been
silently truncated on paste. Only ~94 characters of headroom: if you add a sentence, cut one.

### Why the description is shaped this way

- **The IMPORTANT block is not boilerplate you can trim.** §1 of the checklist identifies the app's
  own Buy/Sell technical meter as the likeliest 5.1.1(ix) / 3.1.5-adjacent rejection hook. Saying
  plainly, on the store page, that the app holds no funds and executes no trades is the cheapest
  possible pre-emption — the reviewer reads it before they open the app.
- **"Free to browse" is stated because it is true and load-bearing.** Guideline 5.1.1(v) requires
  that an app without significant account-based features let people in without a login, and 46% of
  Caydex's routes take no identity at all. Saying so up front frames the account requirement as
  scoped to the metered feature rather than as a wall.
- **The credits paragraph is required by 3.1.1.** Consumables that could read as a currency need
  the "not transferable, not redeemable" language, and it must match the Terms.
- **No persona names.** The Research section describes the lenses by their *method*
  ("quality-and-durability", "growth", "value") rather than by the persona keys, which are still
  real investor names on the wire (`warren_buffett`, `cathie_wood`).

---

## ⛔ Do not use — anywhere in metadata or screenshots

Real investor names: **Warren Buffett · Peter Lynch · Cathie Wood · Bill Ackman · Benjamin Graham ·
Charlie Munger · Ray Dalio · Michael Burry · Joel Greenblatt · Howard Marks · Morgan Housel ·
Robert Kiyosaki** — and any other named individual.

This is not only a copy rule; **five in-app surfaces would put one of these names into a captured
screenshot**:

| Surface | Why |
|---|---|
| Wiser → Book Library / Book Detail | cover art typesets the author's name |
| Wiser → Journey strategies list | three lessons are titled after living investors |
| Wiser → Money Moves | an article is titled "Warren Buffett's Early Days" |
| Tracking → Whales | lists six living investors by name with follow buttons |
| Research → persona picker | check the rendered labels before shooting |

Decide the rename question (plan §7) **before** capturing screenshots, or you will shoot them twice.

Also avoid: "top picks", "best stocks", "beat the market", "guaranteed", "proven returns",
"signals", "buy now", "financial advisor", "portfolio manager", and any % return figure.

---

## Remaining ASC fields this file does not cover

Data entry, but hard-required to reach Submit — and absent from the launch checklist:

- **Copyright** — suggested: `2026 Duc Hai Phan` (must match the seller name on an Individual
  account)
- **Content Rights** — declares whether the app contains third-party content. Substantive here, not
  clerical: it depends on the FMP / CoinGecko redistribution answers and the book-study-guide
  question. Do not answer it before those come back.
- **App Review Information** — first name, last name, phone, email, **and the demo account
  credentials** the review notes promise (plan §8)
- **Age rating** — 17+ via the age-gate question; answer the new AI-content and in-app-chat
  capability questions honestly
- **Availability** — United States only
- **App Review notes** — paste `app-privacy-answers.md` §7 verbatim
