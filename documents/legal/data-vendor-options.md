# What a display licence costs, and what the alternatives actually are

Researched 2026-08-21 against vendors' own pages. Companion to
[`vendor-redistribution-emails.md`](vendor-redistribution-emails.md).

---

## 1. What FMP will probably quote

**Their Enterprise price is not published anywhere credible.** I could not find a single reliable
figure — not on their site, not in reviews, not in forums. Treat any number you see repeated
online as unsourced.

What *is* usable is a market anchor. **Twelve Data is the one major vendor that publishes
business pricing**, and its tiers are labelled by exactly the thing you need:

| Twelve Data tier | Price | What the label says |
|---|---|---|
| Basic | Free | **"Internal non-display usage"** |
| **Venture** | **from $149/mo, list $499/mo** ($4,990/yr) | *"ideal for companies showcasing data on **client-facing apps** or websites"* · **"External display data access"** |
| Enterprise | $1,099/mo ($10,992/yr) | "External distribution market data" |

So the going rate to legally show market data in a consumer app is roughly **$150–$500/month**,
not $49. Expect FMP to land in that region — possibly above it, since their Enterprise tier is
positioned as display **and** redistribution with unlimited volume.

Two things that pull the number down, and both are worth asking for explicitly:

- **Twelve Data publishes a startup discount** (12 months). If they do it, others will discuss it.
- **Delayed or end-of-day pricing is much cheaper than real-time.** Real-time US equity display
  usually drags in exchange fees on top of the vendor's own. If Caydex can live with 15-minute
  delayed quotes, say so in the enquiry — it can move the quote materially.

---

## 2. The finding that matters more than any price

**This is not an FMP quirk. It is how the entire industry is structured.** Checked against each
vendor's own terms:

| Vendor | Structure |
|---|---|
| **FMP** | Personal Use tab (Starter/Premium/Ultimate, "Usage: Individual") vs Commercial Use tab (Enterprise, "Contact Us") |
| **EODHD** | *"The packages on the pricing page are intended for **personal use only** as commercial use requires a more thorough approach to licensing… leave a request for a quote"* |
| **Polygon.io** | Separate "Polygon for Individuals" and "Polygon for Businesses" terms. Individual terms: market data *"cannot be used to build an application intended for use by end users other than you"* → `sales@polygon.io` |
| **Twelve Data** | Free tier is explicitly *"internal non-display"*; display starts at the paid business tier |
| **Tiingo** | *"data cannot be redistributed in any form unless you have a specific redistribution license"* |

**So "switch to a cheaper vendor" does not dodge the licensing conversation** — it only changes
who you negotiate with. Anyone offering you a $20/month plan that lets you display data to the
public is either mispricing or you are misreading their terms. **Read the display clause, not the
price**, for every candidate.

One upside: EODHD says it can onboard a commercial user **in as little as 3 business days**, and
works with startups. So this need not be a months-long process.

---

## 3. What Caydex actually depends on

FMP is load-bearing across ~60 endpoints in 11 domains. Notably, **whale 13F *and* congressional
trading both come from FMP** (`scripts/hydrate_whales.py` → "Fetch raw data from FMP (13F or
Congressional)"), not from EDGAR. The `sec.gov` reference in the codebase is a link on the support
page, not an integration.

| Domain | FMP endpoints | Replaceable? |
|---|---|---|
| Quotes, intraday charts, EOD history | `quote`, `batch-quote`, `historical-chart/{interval}`, `historical-price-eod/full` | Yes — every vendor sells this. **This is the part that genuinely needs a licence.** |
| Fundamentals | `income-statement`, `balance-sheet-statement`, `cash-flow-statement`, `key-metrics(-ttm)`, `ratios(-ttm)` | Yes — or self-source, see §4 |
| Company / search / profile | `profile`, `search-symbol`, `search-name`, `company-outlook` | Yes |
| Analyst | `analyst-estimates`, `price-target-consensus`, `grades` | Partially — fewer vendors |
| Earnings + **transcripts** | `earnings`, `earnings-calendar`, `earning-call-transcript` | Transcripts are **rare and genuinely licensed** (copyrighted works) |
| **13F institutional ownership** | 6 `institutional-ownership/*` endpoints incl. holder analytics | Rare from vendors — **but free at source, see §4** |
| **Insider (Form 4)** | `insider-trading/search`, `insider-trading/statistics` | **Free at source** |
| **Congressional** | `house-disclosure`, `house-latest` | Rare — **free at source** |
| ETF | `etf/holdings`, `etf/info`, `etf/sector-weightings` | Some vendors |
| News | `news/stock`, `news/general-latest`, `news/crypto` | Yes, many |
| Market movers / indices | `biggest-gainers`, `most-actives`, `*-constituent`, `industry-performance-snapshot` | Yes |

The trap: **your differentiators sit on the rarest endpoints.** "App-Exclusive Signals"
(Congressional Buys, Whale Accumulation) and the Whales tab are exactly the data a cheap vendor
does not carry. A naive "swap FMP for X" kills the features that make Caydex not-a-free-tracker.

---

## 4. The asymmetry worth building around

**The differentiating data is public-record and free; the commodity data is what needs a licence.**
That is the opposite of the intuition, and it is the most useful thing in this document.

| Dataset | Source | Licence needed |
|---|---|---|
| 13F institutional holdings | SEC EDGAR | **None** — US government works |
| Insider transactions (Form 4) | SEC EDGAR | **None** |
| Congressional trades | House Clerk / Senate e-filing disclosures | **None** |
| Company fundamentals | SEC XBRL company-facts (`data.sec.gov`) + Financial Statement Data Sets | **None** |
| **Price quotes (real-time or delayed)** | exchange-licensed vendor | **Yes — irreducible** |
| Earnings call transcripts | vendor | **Yes** — copyrighted |
| News articles | vendor | **Yes** |

So a viable end state is a **split**:

- **Buy** a display licence for quotes/charts (+ news, + transcripts if you keep them). This is
  the $150–$500/mo tier, and it is unavoidable.
- **Self-source** 13F, Form 4 and congressional disclosures from EDGAR and the House/Senate
  systems. Free, legally clean, and it makes your moat features independent of any vendor's
  pricing or goodwill.

⚠️ **This is an engineering estimate, not a small one.** EDGAR requires a declared User-Agent,
has rate limits, and 13F/Form 4 parsing is genuinely fiddly (that is precisely what you are paying
FMP to have solved). Do not treat it as a weekend. But it is a *known* cost you control, versus a
recurring fee you do not.

---

## 5. What I would do, in order

1. **Send the FMP enquiry today** and ask three things explicitly: the display-licence price; whether
   **delayed/EOD** quotes cost less than real-time; and whether they offer **startup pricing**.
2. **In parallel, ask Twelve Data for their startup discount.** Published pricing makes them a
   real negotiating anchor rather than a bluff — and if FMP's number is silly, you have somewhere
   to go.
3. **Before either quote lands, work out how much of the app survives without FMP.** That number
   is your leverage in both conversations, and you cannot negotiate without it.
4. **Treat EDGAR self-sourcing as the strategic hedge** for whales/congress/insider. Even if you
   sign with FMP now, owning that pipeline caps your future exposure on the features you most
   depend on.

---

## Sources

- FMP: [Terms of Service](https://site.financialmodelingprep.com/terms-of-service) · [Pricing](https://site.financialmodelingprep.com/developer/docs/pricing)
- Twelve Data: [Business pricing](https://twelvedata.com/pricing-business)
- EODHD: [Commercial vs Personal license use](https://eodhd.com/financial-apis/commercial-vs-personal-license-use)
- Polygon.io: [Individuals ToS](https://polygon.io/legal/individuals-terms-of-service) · [Businesses ToS](https://polygon.io/legal/businesses-terms-of-service)

**Not legal advice.** Vendor terms change; confirm anything here in writing with the vendor before
relying on it.
