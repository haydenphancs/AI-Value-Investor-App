# FMP — reply to Laith (sent 2026-08-25)

Short version, sent in place of the longer draft. Full reasoning and call-site traces:
`~/.claude/plans/check-launch-checklist-md-i-have-sprightly-tarjan.md`.

## Decisions taken

- **Transcripts:** drop. Only 2 consumers, both already on shipped fallbacks.
- **Bandwidth:** ask for **150 GB**, not 100. Trailing 30d is 25.57 GB but that is *pre-launch*
  (dev traffic only). 100 GB is ~4× headroom for "low thousands of users"; 150 GB is ~6×. The tier
  price gap is small, the risk gap is not — a hard stop mid-month is an outage for every paying
  user, and month-to-month means we can negotiate down later. Caching is already optimised
  (batch-quote fix: cold Home ~203 → ~5 calls; ETF/index/commodity caches landed), so there is
  little slack left if we undershoot.
- **Real-time / streaming:** drop both. Never needed, and streaming is what triggers the
  $1,500–2,000/mo exchange fees.
- **Quote endpoint: DO NOT agree to drop.** `/stable/quote` + `/stable/batch-quote` are 16 and 12
  backend callers, and indices / commodities / crypto all ride those same two paths — dropping
  them removes every current price on every asset class.

## The three things to settle before confirming

1. Is "the quote endpoint" the same line item as "the real-time endpoint"? He used both phrases
   interchangeably; *endpoint* and *latency* are different things.
2. Can `quote` / `batch-quote` be served **15-minute delayed** without exchange licences? He
   priced only real-time and skipped straight to removal.
3. ⚠️ His email #2 said the separate Data Display and Licensing Agreement isn't required
   *"provided we remove the quote endpoint."* **If we keep it, does that waiver come back?** This
   gates the App Store **Content Rights** answer — do not answer it in ASC until this is in writing.

Plus: the streaming correction (we told him we consume no streaming feed; `live_price_manager.py:30-31`
says otherwise), and the Advanced Market Metrics entitlement check (5 endpoints called today at
`fmp.py:732, 827, 850, 859, 868` — a 403 risk in production).

Deferred to the Order Form review, not worth asking now: notice period, cancellation access, the
ToS §6.3 vs exported-PDF tension, index/commodity symbol pricing, mixed-batch 403 behaviour.

---

## The email

Subject: Re: Caydex — Enterprise scope

Hi Laith,

Thanks — that's clear, and month-to-month helps a lot.

Confirming two of the three:

1. Earnings call transcripts. Yes, please remove them.

2. Bandwidth. Yes, please reduce it. 150 GB / 30 days would suit me. Could you tell me what
   happens if I go over — throttle, overage charge, or hard stop?

3. The quote endpoint. This is the one I'm unclear on, and I'd appreciate a bit more detail before
   I confirm.

We'll remove the real-time endpoint. But end-of-day quotes would remove a lot of important
features in my app — indices, commodities and crypto all come back from those same quote
endpoints, so losing them takes out far more than stock prices.

So: can the quote endpoint be provided on a 15-minute delayed basis (or whatever delay you can
offer), without the exchange licences? Delayed is completely fine for me — this is a research app,
not a trading app, and my Terms of Use already disclose a 15–20 minute delay.

One related question, since it affects my App Store submission: in your earlier email you said a
separate licensing agreement isn't required provided we remove the quote endpoint. If we keep it
on a delayed basis, does that change?

And one correction I owe you. I previously said we don't consume a streaming feed. That was wrong
— my backend does have a WebSocket client for your streaming endpoints. It has never actually
worked (my key isn't entitled, it returns 401) and I'm removing the code before launch, but I'd
rather tell you now than have it come up later. Please leave streaming out of the package.

Once that's clear I can confirm everything else and we can move to the formal quote.

Best regards,
Hayden

---

## Deferred to the next round — do not lose these

- **Advanced Market Metrics entitlement.** He said they "were not included in the original scope."
  The backend calls five today (`fmp.py:732, 827, 850, 859, 868`). If that means *not entitled*,
  they 403 the day we switch keys.
- **Order Form items:** notice period · access on cancellation · the ToS §6.3 "delete all cached
  data on termination" vs exported PDFs on users' devices · index/commodity symbol pricing ·
  mixed-batch 403 behaviour (`_fetch_chunk` swallows a chunk failure into `[]`, so a rejected
  chunk silently blanks every quote in it).

## WebSocket removal — verified 2026-08-25, safe to delete

Probed live: both FMP sockets return **401 Unauthorized** on the real key, and Railway's
`FMP_API_KEY` **hash-matches** the local one (`4e5bef790fa928bd`), so prod is the same unentitled
key. Production's own `/ws/price/BTCUSD` connected and stayed **silent for 10s** on a 24/7 symbol.
**No view anywhere reads `isConnected`** — nothing on screen reflects socket state. Every detail
screen already runs a REST refresh (ticker 15s poll; ETF/index/commodity 30s `refreshLiveSlice`).

Removal surface (~half a day): 5 ViewModels to unwire · delete `live_price_manager.py` (492),
`live_price.py` (177), `LivePriceWebSocketManager.swift` (239), `LivePriceModels.swift` (25) ·
unregister `api.py:26,67` + `main.py:29,311` · delete `test_live_price_previous_close.py` +
`test_live_price_ws_routing.py` · drop the assert at `test_detail_screen_outliers.py:1225` (its
real subject is `chartRefreshTask`, unaffected) · optional: `app/services/certs/fmp_ws_intermediates.pem`.

Best done *after* his reply — a delayed-quote answer means touching those same five ViewModels for
a delay badge, so it's one pass instead of two.

---

## Round 3 — sent 2026-08-25

**His reply:** 15-min delayed, exchange-fee-free, available for **CBOE** and **IEX** exchanges
only — and **WebSocket only, no REST**. Offered to include it in the quote.

**Read:** CBOE (~8.6% of US volume) + IEX (~3.2%) are US *equity* exchanges. They carry stocks and
ETFs. They cannot carry indices (`^GSPC`/`^IXIC` are calculated, not traded), commodity futures
(COMEX/NYMEX), or crypto. A trade stream also cannot produce `yearHigh`/`yearLow`/`marketCap`/
`avgVolume`/`priceAvg50/200` — no stream ever can.

**Verdict: WebSocket ALONE is not viable. WebSocket + the historical REST endpoints IS.**
`historical-price-eod` + `historical-chart` + `profile` are a *different endpoint family* from
`quote`. Keep them and indices/commodities/crypto keep working unchanged, yearHigh/yearLow compute
from EOD, and marketCap/beta come from `profile` (already wired as a fallback at
`stock_overview_service.py:773-784, 885-895`). Lose them and nothing saves the app.

⚠️ **He has now dodged the historical question twice.** Round 3 asks it alone, as a yes/no.

**Not established, despite appearances:** it is NOT "WebSocket or no deal." He said delayed-
*without-exchange-fees* is WS-only; he never said REST quote cannot be delayed at all.

**Measured 2026-08-25:** live DB has only **15 distinct symbols** across all watchlists, holdings
and alerts — but with ~4 users that says nothing about launch scale. The subscription count at
1,000 users is what would decide whether a WS ingestion service is buildable, and it is unknown.

### The email

Hi Laith,

Yes please — include the CBOE and IEX delayed WebSocket options in the quote so I can see what
they add.

One question I still need answered before I can confirm anything. I don't think it came through in
my last two emails, so let me ask it on its own:

If the REST quote endpoints are removed from my plan, do these two remain?

  - /stable/historical-price-eod
  - /stable/historical-chart/{1min, 5min, 15min, 30min, 1hour}

That is the whole decision for me. I use them for every chart in the app, and also for indices
(^GSPC), commodities (GCUSD, CLUSD) and crypto (BTCUSD) — none of which a CBOE or IEX equity feed
would carry. If I keep those two endpoints I can make everything else work. If I lose them, the
WebSocket doesn't help me.

A simple yes or no is all I need.

Best regards,
Hayden

---

# Round 4 — response to the formal quote — ✅ SENT

**Awaiting reply.** Three answers decide whether this is signable:
1. **Term** → `Initial Subscription Term: Monthly` on the document, not just in email.
2. **§6 AI Prompts named on the Order Form** → without it the headline paid feature is unlicensed.
3. **4(a) intraday bars during the session** → asked four times now; if EOD-only, there is no
   current price for any asset class and the CBOE/IEX WebSocket becomes mandatory, unpriced.

⚠️ **Offer expires 2026-09-27.**

**The quote:** $29,500 list → 44.9% discount ($13,245 ÷ $29,500, not the stated 55%) →
**$13,245/yr = $1,104/mo**, `Initial Subscription Term: Annual`, `Renewal Term: Annual`.

**Two headline problems.** (1) 1.84× the **$600** and 2.2× the *"approximately $500"* quoted by
email. (2) Laith wrote *"changed your commitment from annual to monthly"* — but he changed
**Billing Frequency**, not the term. §1 defines Fees as payable *"each **Term**"*, so signing
commits **$13,245**, not $1,104.

⚠️ **The term fix needs three amendments, not one.** §9.1 runs twelve months *"or until the expiry
of the Initial Subscription Term set forth on the Order Form, **whichever is longer**"* — so editing
the Order Form line alone is defeated by §9.1, and editing §9.1 is defeated by §4.1
(*"non-cancelable and non-refundable regardless of any early termination"*). §12.7 requires each
amended section to be cited by number.

🔴 **Three blocking licence gaps** (see `fmp-quote-analysis.md` §6):
1. Exhibit A: *"Licensee is authorized only for those Permitted Uses expressly identified in the
   applicable Order Form."* **The Order Form identifies none** — its only licence line is
   "End-User Display Rights", which is not one of Exhibit A's numbered uses.
2. Exhibit A §6 permits AI prompts *"provided that AI Prompt use is identified as a Permitted Use
   on the Order Form."* **It is not.** The headline paid feature pipes FMP data into Gemini.
3. **The PDF export is an express Non-Permitted Use**, three times over — §1(i) *"providing any Data
   … in any downloadable fashion"*, plus Exhibit A §2 and §3 each saying *"redisplay only … does not
   authorize … downloadable files."* Shipped today at `research.py:579` → `ReportPDFViewModel` →
   `UIActivityViewController`. And §9.3's deletion duty covers data *"in the possession or control of
   any … Authorized Viewer"* — unperformable once a PDF is AirDropped.

**Offer expires 2026-09-27.** §8.1 indemnity is carved out of the liability cap by §7.3/§7.4.

## The email

Subject: Re: FMP Quote — Caydex — 0001

Hi Laith,

Thanks for putting this together, and apologies for the slow reply — I wanted to read the whole
document properly rather than respond to the headline number, and the licence terms took a while
to work through.

I'll be straightforward: at $1,104 a month it's well over my budget, and I can't sign it as
drafted. I do think most of the gap is fixable, though. Five things.

**1. The term on the Order Form.**

You'd mentioned a month-to-month term, so I may be misreading this — but the Order Form says
`Initial Subscription Term: Annual` and `Renewal Term: Annual`, with `Billing Frequency: Monthly`.
That reads to me as monthly payments on an annual commitment, which would commit me to $13,245
rather than $1,104.

An annual commitment isn't something I can take on. Could we put the monthly term on the document
itself? Sections 4.1, 9.1 and 9.3 all bear on it, so I think it needs both of these:

  - Order Form: **Initial Subscription Term: Monthly**
  - Special Terms: "Notwithstanding Sections 4.1, 9.1 and 9.3, the Initial Term is month-to-month;
    this Agreement shall not automatically renew without Licensee's written consent; and upon
    termination by Licensee under Section 9.2(a), Licensee's sole payment obligation shall be the
    monthly installments invoiced for periods through the effective date of termination."

**2. Please remove three packages.**

  - Mutual Funds Holdings
  - Indexes
  - Commodities

Could you send the revised total with those removed?

**3. Three licence points I need fixed before signing.**

Exhibit A says "Licensee is authorized only for those Permitted Uses expressly identified in the
applicable Order Form, and no other Permitted Uses shall be implied" — but the Order Form doesn't
identify any of the numbered uses. Its only licence line is "End-User Display Rights". So:

  a) Please name the Permitted Uses on the Order Form. I believe I need four of them:

       - **§2 Access Restricted External Display** — signed-in screens
       - **§3 Public External Display** — my market-data screens are browsable without an account
       - **§5 Modeling / Derivation for External Use** — my AI reports are sold to users, and §4
         expressly forbids commercializing Derived Products, so §5 is the one that fits
       - **§6 AI Technology – AI Prompts** — see (b) below

     I don't need §7 (Model Training); I don't train anything on your data.

  b) **AI Prompt use.** Exhibit A §6 permits sending Data to AI "provided that AI Prompt use is
     identified as a Permitted Use on the Order Form" — and it isn't. My core paid feature sends
     your data to an LLM and shows the written output to the user. §6 also limits this to "internal
     business purposes", which doesn't describe generating a report for a paying customer. Please
     identify §6 on the Order Form and confirm it covers outputs displayed to end users. If that
     changes the price, I'd rather know now.

  c) **PDF export.** My app lets a user export a saved report as a PDF to their own device. §1
     lists "providing any Data to any party in any downloadable fashion" as a Non-Permitted Use, and
     Exhibit A §2 and §3 both say the display right is "redisplay only". Can this be expressly
     permitted on the Order Form? Related: §9.3 requires deleting all Data including copies held by
     Authorized Viewers within 30 days of termination — a PDF already on someone's phone can't be
     recalled, so I'd need that obligation limited to data within my own systems.

**4. How I plan to show prices.**

With real-time quotes out, I'd take the displayed price from the last bar on
`/stable/historical-chart` and the previous close from `/stable/historical-price-eod` — both
endpoints my charts already use.

  a) **Does that work while the market is open?** I need today's intraday bars available during
     the session, not only after the close. Package 7 is marked "Data Delay: EOD", so I want to be
     sure that isn't a problem.

  b) **Does Historical and Intraday Market Data cover crypto pairs like BTCUSD, or US equities
     only?** Crypto prices aren't listed in any package, though Crypto News is inside Market News.

**5. Two smaller things.**

  a) "Refer to Cycle Times" appears four times and Cycle Times aren't defined anywhere in the
     document. Could you send the table?

  b) The display licence covers 1,000 unique users per month, and §2.6 adjusts fees upward
     automatically when that's exceeded. What's the next tier and what does it cost? I'd rather know
     the growth curve before signing than discover it later.

I know that's a long list. The term, the AI-prompt point and question 4(a) are the three I
genuinely can't proceed without; the rest is scope and clarity. If we can land those and get the
number into range, I'm ready to move.

Best regards,
Hayden
