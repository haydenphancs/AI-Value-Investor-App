# Data-redistribution licensing enquiries — FMP and CoinGecko

Send both **today**. The answers feed the App Store Connect **Content Rights** declaration (§7),
which is on the submission path — do not answer that question before these come back.

## ⚠️ Why this is not a formality — PRIMARY SOURCE, re-checked 2026-08-21

Re-researched after a fair challenge ("I pay monthly, surely I can build my app with it"). That
intuition is how most developer APIs work — Stripe, Firebase, OpenAI. **Market-data vendors are
the exception**, because they are themselves licensees of the exchanges and filers whose data
they resell; they cannot grant display rights they do not hold without a separate agreement. The
same personal-vs-display split exists across the industry, not just at FMP.

Read directly off FMP's own pages (not a summary site):

**Their pricing page has two tabs, and Premium is on the wrong one.**

| Personal Use tab | Commercial Use tab |
|---|---|
| Basic (free), Starter $19, **Premium $49**, Ultimate $99 | **Enterprise** — "Contact Us" |
| Comparison table is headed *"Compare **Individual** Use Plans"* | Headed *"Commercial Use Plan"* |
| `Usage` row = **"Individual"** on all four | `Usage` row = **"Commercial - Display and Redistribution"** |

The Enterprise card's own description: *"Perfect if you're looking for **data display and
redistribution**, unlimited volume, and priority support."* It is a sales contact form, not a
checkout — it asks for Company Name, Company Website, Company Type and Country of Registration.

**The two clauses that decide it**, quoted verbatim from the Terms of Service (last updated
August 1, 2023):

> **2.2.1 Personal Use:** "This license may only be used by a Customer who is an individual, and
> strictly for their own personal, non-business and non-commercial purposes. … the Customer may
> not … **integrate the Data or Services into any tools or applications accessible by any third
> parties**, or use the Services to host, share, display, or provide content for others."

> **2.2.2 Data Display:** "Without a specific agreement with FMP, customers are prohibited from
> showcasing FMP Services or Data on platforms including but not limited to websites, blogs,
> software products, or **applications designed for utilization by multiple individuals,
> irrespective of whether such usage is complimentary or paid**…"

A public App Store app is definitionally "accessible by third parties" and "designed for
utilization by multiple individuals". The "complimentary or paid" wording also closes the
obvious workaround — making the app free would not help.

And the footnote repeated on **both** pricing tabs: *"Displaying or redistributing data sourced
from FMP requires a specific Data Display and Licensing Agreement with FMP."*

**The real risk is not Apple — it is §2.10.** FMP reserves the "Right to Monitor" and may
"downgrade, suspend, or terminate Customer's access" on suspicion of a violation. If they pull
the key after launch, the app's core data disappears for every paying user at once. That is a
worse failure than a rejected submission.

**CoinGecko** is the same shape and lower stakes: the free **Demo** plan is positioned for
testing and exploration; commercial use begins at the paid tiers (Basic and up), which also
require a visible *"Data provided by CoinGecko"* credit and a link.

### What this realistically means

- The FMP conversation is a **quote**, not a yes/no — and Enterprise pricing is not published.
- Their form wants a **company name, website and country of registration**, which pushes on §1's
  LLC question. Worth deciding those together rather than twice.
- If the number comes back beyond an indie budget, the honest options are: form the entity and
  negotiate; **look at vendors whose paid tiers permit display at indie scale** (worth pricing
  before you commit — do not assume, check each one's display clause the same way); or narrow
  what the app shows from FMP. Do that comparison *before* you sign anything.

---

## 1 — Financial Modeling Prep

**To:** the address on your plan's billing/support page (or the in-app support widget on
financialmodelingprep.com — use the account the API key belongs to, so they can see your plan)
**Subject:** `Data Display & Licensing — commercial mobile app, current plan coverage`

```
Hello,

I'm the developer of Caydex, an iOS app that presents company fundamentals and market
data to retail users. I'm preparing to launch on the App Store and want to confirm my
licensing position before I do.

I currently subscribe to Premium (account: [YOUR ACCOUNT EMAIL]). My reading of your
terms is that Starter/Premium/Ultimate are personal-use licences and do not cover
displaying data to end users, and that a commercial plan plus a Data Display and
Licensing Agreement is required for what I'm doing. I would like to correct that before
I launch rather than after.

Three specific questions:

1. Which plan do I need? The app shows FMP-sourced data (quotes, company profiles,
   income statement / cash flow figures, ratios, analyst estimates, insider and
   institutional filings) directly to end users inside a consumer mobile app. It has a
   paid subscription tier and consumable in-app purchases, so it is a commercial
   product. Please confirm which commercial plan covers this and what it costs, and
   whether a separate Data Display and Licensing Agreement is also required.

2. Server-side caching. My backend caches FMP responses in my own database to stay
   within rate limits and to keep response times low. Cached values are refreshed on a
   TTL (minutes to 24 hours depending on the endpoint) and are only ever served to my
   own app's users. Is that within the terms of my plan?

3. Point-in-time snapshots. When a user generates a research report, the app freezes the
   figures used into that report so the document stays internally consistent when it is
   re-opened later. That means some FMP-derived numbers persist beyond the cache TTL,
   attached to that one user's report. Please confirm whether that is acceptable, or
   whether you would require those values to expire.

I attribute Financial Modeling Prep as a data source in-app and on my support page, and
I'm happy to adjust the attribution wording to whatever form you require.

If any of the above needs a different plan or a separate agreement, please let me know
what that is and I'll get it in place before launch.

Thank you,
[YOUR NAME]
Caydex — caydexinvest.com
[YOUR EMAIL]
```

---

## 2 — CoinGecko

**To:** the support address for the Demo/free plan (support@coingecko.com, or the contact form
at coingecko.com/en/api — again, use the account the key belongs to)
**Subject:** `Demo plan — commercial use and attribution for a paid iOS app`

```
Hello,

I'm the developer of Caydex, an iOS app that includes cryptocurrency market data
alongside equities. I'm preparing to launch on the App Store and want to confirm my
licensing position first.

I'm currently on the free Demo plan (account: [YOUR ACCOUNT EMAIL]).

My questions:

1. Commercial use. The app has a paid subscription tier and consumable in-app
   purchases. Crypto data is one feature among many, not the product itself. My reading
   is that the Demo plan is for testing and exploration and that commercial use begins
   at the paid tiers — please confirm, and if so tell me the lowest tier that covers
   this use and its price.

2. Display and caching. The app shows CoinGecko-sourced values (price, market cap,
   circulating and total supply, FDV, 24h volume) to end users, and my backend caches
   responses in my own database on a short TTL to stay under the rate limit. Both are
   for my own app's users only — I do not redistribute the data to third parties or
   expose it through an API of my own. Is that within the Demo plan terms?

3. Attribution. I currently credit CoinGecko as a data source in-app and on my support
   page. Please confirm the exact attribution wording and placement you require, and
   whether a link back is mandatory.

If the Demo plan does not cover this, please point me at the tier that does and I'll
upgrade before launch.

Thank you,
[YOUR NAME]
Caydex — caydexinvest.com
[YOUR EMAIL]
```

---

## When the replies land

- **Both say yes** → answer ASC **Content Rights** as "contains third-party content, and I have
  the rights to use it", and keep the emails.
- **Either says you need a paid tier or a separate agreement** → do that first. Launching against
  a "no" is a contract problem that an App Store approval does not cure.
- **Either asks for specific attribution wording** → update the in-app credits and
  `documents/legal/` support page to match verbatim, then re-verify
  `tests/test_legal_pages.py::test_support_page_names_every_data_source` still passes.
