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

⚠️ **It is a FORM, not an email.** The commercial channel is the Enterprise card on
<https://site.financialmodelingprep.com/developer/docs/pricing> → **Commercial Use** tab. There is
no "contact sales" address; the form is the front door. (`info@financialmodelingprep.com` from
ToS §2.7 is general support — use it only as the fallback described below.)

### The exact fields it asks for

| Field | What to put | Note |
|---|---|---|
| First Name / Last Name | your legal name | must match whatever you'd sign an agreement as |
| **Corporate Email** | **whatever the form prefills — do not fight it** | The field is **locked to your FMP account address** (`haydenphancs@gmail.com`). That is *good*: it links the enquiry to your existing **paying Premium subscription**, so you arrive as a customer upgrading rather than an anonymous lead. A free-provider domain matters far less than that. Put `support@caydexinvest.com` in the use-case text as the preferred reply-to if you want correspondence on the domain. |
| **Company Name** | `Caydex` | See the note below — this is the field that trips you up. |
| Company Website | `https://caydexinvest.com` | You have this and it serves a real site — a genuine advantage; most tyre-kickers don't. |
| Company Type | closest match in the dropdown | If there is no *Individual* / *Sole Proprietor* option, pick the nearest and say so explicitly in the use-case box. |
| Your Job Title | `Founder` | |
| **Country of Registration** | `United States` | |
| Describe Your Use Case | the block below | This is the only field that does any work. |

### ⚠️ "Company Name" when you have no company

You are an **Individual** Apple developer with no LLC (§1). Do not invent an entity — you may end
up signing a licensing agreement against that name, and a mismatch between the signer and the
legal person is a real problem later.

Put **`Caydex`** (your product/trading name) and then **say the true thing in the use-case box**:
that you currently operate as an individual and can form a US LLC if the agreement requires a
legal entity. That is accurate, it is not a red flag to a vendor, and it surfaces the entity
question *before* you're mid-contract — which is exactly when you want it, because it is the same
decision as §1.

### Paste into "Describe Your Use Case"

Keep it tight; it is a textarea, not a letter. This covers the three things they need to quote.

```
I run Caydex (caydexinvest.com), a consumer iOS app for retail investors, not yet
launched. I'm writing from the email on my existing FMP subscription so you can see the
account; support@caydexinvest.com also reaches me if you prefer a domain address.

I'm currently on the Premium personal plan and I understand from your terms
(2.2.1 / 2.2.2) that displaying data to end users needs a commercial plan plus a Data
Display and Licensing Agreement. I want to be licensed correctly before I ship rather
than after, so I'd like a quote.

Use case:
- The app shows FMP-sourced data directly to end users: quotes, company profiles,
  income statement / cash flow / ratios, analyst estimates, insider and institutional
  (13F) filings, ETF data.
- It is a commercial product: a paid subscription tier plus consumable in-app purchases.
- Expected scale at launch is small - low thousands of users, US App Store only.
- My backend caches responses in my own database on a TTL (minutes to 24h by endpoint)
  to stay inside rate limits. Data is served only to my own app's users; I do not
  re-expose it through any API of my own.
- Generated research reports freeze the figures used into a point-in-time snapshot, so
  some values persist past the cache TTL inside one user's saved report. Please tell me
  if that needs to expire.

Two things I'd like in the reply: (1) which plan covers this and its cost, and (2)
whether a separate Data Display and Licensing Agreement is required on top.

I currently attribute FMP as a data source in-app and on my support page, and will
adjust the wording to whatever form you require.

Note on entity: I operate as an individual today (no LLC). If the agreement requires a
registered legal entity I can form a US LLC - please tell me if that is a prerequisite.
```

### If the form blocks you

If a required field genuinely cannot be filled honestly, **do not fake it.** Send the same text
to `info@financialmodelingprep.com` from `support@caydexinvest.com`, subject *"Commercial /
Data Display licensing enquiry — Caydex (existing Premium customer)"*, and say the form's
company fields don't fit an individual. Support routing is slower than the sales form, so try
the form first.

---

## 2 — CoinGecko

CoinGecko *does* take email. Send from `support@caydexinvest.com`, using the account the API key
belongs to.

**To:** `support@coingecko.com` (or the contact form at coingecko.com/en/api)
**Subject:** `Demo plan — commercial use and attribution for a paid iOS app`

**Do this one second, and do not let it hold anything up.** It is far lower stakes than FMP: the
paid tiers are published and affordable, crypto is one feature among many rather than the core of
the app, and the worst case is "upgrade to Basic and add an attribution line" — not a negotiation.

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

## After you send

**Expect a sales conversation, not a yes/no.** Typical shape: auto-acknowledgement, then a rep
within a few days asking about volume, endpoints and user count, then a quote plus an agreement
to sign. Days to a couple of weeks, mostly waiting on them.

**They are very unlikely to refuse you** — you are a paying customer volunteering to pay more.
The risk is the *price*, not rejection. And asking now, pre-launch, is a materially different
conversation from asking later: today you are a developer getting licensed before shipping;
after launch the same message discloses an ongoing violation on a live product with paying users.

### Keep building while you wait — only ONE thing is blocked

Everything else in App Store Connect is independent: upload the build, listing copy, screenshots,
pricing, availability, App Privacy, age rating, review notes, demo credentials, IAP metadata and
subscription-group localization. Do all of it now.

The single blocked field is **Content Rights**, and it is hard-required to reach Submit. So in
practice FMP's reply gates *submission*, not *preparation* — get everything else done and you are
one checkbox away when they answer.

### When the replies land

- **Yes / you're covered** → answer ASC Content Rights, and **keep the written reply** — it is the
  evidence behind the declaration.
- **You need a commercial plan** → decide with the §1 LLC question, not separately; their form
  already asked for a country of registration.
- **The quote is out of range** → three routes, cheapest first: (1) reduce FMP surface — you also
  pull FRED, FINRA, EDGAR and CoinGecko, so work out how much of the app survives without FMP
  before negotiating, because that is your leverage; (2) switch or split vendors — but read each
  one's **display clause** specifically, not just its price, which is the exact trap that produced
  this document; (3) form the LLC and negotiate as an entity.
- **Attribution wording is specified** → update the in-app credits and the support page verbatim,
  then re-run `tests/test_legal_pages.py::test_support_page_names_every_data_source`.

⚠️ **Not legal advice.** Whether to ship before an answer is a business decision with real
exposure — a Content Rights declaration you cannot support, and ToS §2.10 letting FMP terminate
the key under a live user base. It sits next to the investment-adviser question on the
"needs a real lawyer" list; one conversation could cover both.
