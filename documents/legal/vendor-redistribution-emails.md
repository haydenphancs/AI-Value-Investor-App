# Data-redistribution licensing enquiries — FMP and CoinGecko

Send both **today**. They are free, and the answers feed the App Store Connect **Content Rights**
declaration (§7), which is on the submission path — you should not answer that question before
these come back.

Ask for the answer **in writing**. Keep the reply; it is the evidence behind the declaration.

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

I currently subscribe to [YOUR PLAN NAME] (account: [YOUR ACCOUNT EMAIL]).

Three specific questions:

1. Display to end users. The app shows FMP-sourced data (quotes, company profiles,
   income statement / cash flow figures, ratios, analyst estimates, insider and
   institutional filings) directly to end users inside a consumer mobile app. The app
   has a paid subscription tier and consumable in-app purchases, so it is a commercial
   product. Does my current plan cover that display, or does it require a separate Data
   Display and Licensing Agreement?

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
   purchases. Crypto data is one feature among many, not the product itself. Does the
   Demo plan permit use inside a commercial app, or do I need a paid tier before I can
   charge users?

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
