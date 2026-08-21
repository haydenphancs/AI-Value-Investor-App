# App Store screenshots — Caydex

Captured 2026-08-20 from **iPhone 17 Pro Max**, simulator UDID
`3C473C18-1FB5-417F-836B-3D0EFDFB7026`, at **1320 × 2868** — the 6.9" App Store size.
Status bar overridden to the 9:41 marketing convention (`simctl status_bar override`).

## Only 6.9" is required

Apple now accepts **one iPhone set at the largest size** and scales it down to every smaller
iPhone shelf. `LAUNCH_CHECKLIST.md` §8/§9 said "6.9" and 6.5"" — that is stale. No 6.5"
simulator (iPhone 11 Pro Max / XS Max) is installed on this machine, and none is needed.

## What is here

| File | Screen | Notes |
|---|---|---|
| `6.9/01-home-dashboard.png` | Home | Live index strip, Daily Scanners, App-Exclusive Signals |
| `6.9/02-ai-research-personas.png` | Research | The five **style-named** personas — no real investor names |
| `6.9/03-ticker-detail-nvda.png` | Ticker detail | Live intraday chart + Key Statistics, fully loaded |
| `6.9/04-updates-ai-insights.png` | Updates | AI Insights card + live news timeline. ⚠️ The news rows below the card carry third-party headlines that can name real investors (this capture has "Ken Griffin says Citadel unwound…" partly visible at the fold). That is factual reporting by a news source, not the app naming a feature after a person — a materially weaker 5.2.1 exposure than a lesson title was — but if you want it airtight, crop to the Insights card or re-shoot when the feed rotates. |

These are **raw device captures**, not marketed screenshots. Apple accepts them as-is; most apps
add a caption band and a device frame. Do that pass before uploading if you want it.

## Still to capture

Two, and **both need data that a fresh signed-out install does not have**:

1. **Add Credits** — *required* for the four consumable IAPs (§6b); one shot showing all four
   packs satisfies all four products. Needs a signed-in account, so shoot it with the App Review
   demo account once that exists.
2. **Tracking** — on a fresh install it renders the "No tickers yet" empty state, which is not
   worth shipping. Add three or four tickers to the watchlist first, then re-shoot.

A Learn/Journey card is also worth having now that the lesson titles are clean — the Strategies
row reads "The Quality Compounder / The Everyday Growth Hunter / The Disruption Seeker".
`_verification/journey-renamed-lessons.png` shows that row, but it was shot as evidence rather
than as marketing, and the Level 4 block beneath it contains the Buffett quote card.

## ⚠️ Screens to keep OUT of frame (Guideline 5.2.1)

The three Journey lesson titles were renamed on 2026-08-20, but these still put a real person's
name on screen:

- **Whales / 13F tab** — 56 named real investors and 11 sitting politicians
- **Book-cover shelf** — covers typeset real author names
- **Journey screen, investor quote card** — attributed `— Warren Buffett`
  (`InvestorPathModels.swift:428`); it sits below Level 4, so the Journey screen is only safe to
  shoot above that fold
- **SmartMoneyInfoSheet** (`:60`) and **ShareholderBreakdownInfoSheet** (`:250`)
