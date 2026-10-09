# App Store screenshots — Caydex

## ▶ Current set: `6.9-v2/` (version 1.01) — upload these, in this order

Captured 2026-10-08 by `frontend/ios/scripts/capture-store-screenshots.sh` (never by hand). The
order is the app's own tab bar, which is what users see first: App Store Connect keeps the order
you set, and with no app preview the first three are also the ones search results show.

| # | File | Screen | What it shows |
|---|---|---|---|
| 1 | `6.9-v2/01-home.png` | Home | Sample market strip, watchlist and Daily Scanners on fictional companies; App-Exclusive Signals locked |
| 2 | `6.9-v2/02-updates.png` | Updates | Sample Insights card and News Tone chart (30D); every Updates read canned |
| 3 | `6.9-v2/03-research.png` | Research | The persona picker — investing styles, no real investor named |
| 4 | `6.9-v2/04-tracking.png` | Tracking | Sample watchlist on fictional companies |
| 5 | `6.9-v2/05-wiser.png` | Wiser | Investor Journey + Money Moves (live article titles: re-check them on every re-shoot) |

Every price, % move and chart is invented and sits on a FICTIONAL company (no "Sample data" label,
owner 2026-10-08). `backend/tests/test_ios_store_screenshot_mode_debug_only.py` pins the fixtures,
the fictional tickers and this order. Everything below documents the **1.0 set** (`6.9/`), which
showed real prices — never upload it again.

## The 1.0 set (`6.9/`, 2026-08-20)

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
| `6.9/06-tracking-watchlist.png` | Tracking | Populated watchlist (AAPL/CRM/ORCL/PLUG) with sparklines, plus Alerts & Upcoming Events. Whales cards here name **tickers only**, so it is 5.2.1-safe. |
| `6.9/05-add-credits.png` | Add Credits | All four consumable packs, real StoreKit prices, 3.1.1 disclosure. **Required for the IAP submission.** |
| `6.9/04-updates-ai-insights.png` | Updates | AI Insights card + live news timeline. ⚠️ The news rows below the card carry third-party headlines that can name real investors (this capture has "Ken Griffin says Citadel unwound…" partly visible at the fold). That is factual reporting by a news source, not the app naming a feature after a person — a materially weaker 5.2.1 exposure than a lesson title was — but if you want it airtight, crop to the Insights card or re-shoot when the feed rotates. |

⚠️ **2026-09-28 — recapture before the listing goes live:** FMP permits no public display of
prices. `01` (live index strip), `03` (NVDA price + intraday chart), `04` (the ticker chip's % move)
and `06` (watchlist prices + sparklines) show real prices, % moves or charts; re-shoot them with
labelled sample values. Market cap, P/E and other non-price figures may stay.

These are **raw device captures**, not marketed screenshots. Apple accepts them as-is; most apps
add a caption band and a device frame. Do that pass before uploading if you want it.

## ✅ Add Credits — captured 2026-08-21

`6.9/05-add-credits.png` shows all four consumable packs with real prices ($2.99 / $5.99 /
$12.99 / $24.99), Buy buttons, and the Guideline 3.1.1 line *"Purchased credits never expire.
Your monthly credits reset each month."* **One shot satisfies all four IAP products.**

How it was produced, because two earlier attempts were rejected on size:

1. **Real prices need an Xcode-driven run.** `Caydex.storekit` is attached to the scheme's *Run*
   action only; `simctl launch` ignores it and every pack renders "Price unavailable". The
   automated equivalent of ⌘R (works, but takes ~2.5 min before the app appears — do not
   conclude it failed early):
   ```bash
   osascript -e 'tell application "Xcode" to tell workspace document "ios.xcodeproj" to \
     debug scheme "ios" run destination specifier "id=3C473C18-1FB5-417F-836B-3D0EFDFB7026"'
   ```
   Confirm it took: `xcrun simctl spawn <UDID> log show --last 10m --predicate 'process == "storekitd"' | grep -c XcodeTest`
   must be **> 0**.
2. **Capture with `xcrun simctl io <UDID> screenshot`**, which always writes true device
   resolution. Simulator ⌘S also works. ⇧⌘4 does **not** — it grabs the scaled macOS window.

## Complete — six shots, all 1320 x 2868

Apple asks for 3-10 per size, so six is a shippable set. Nothing is outstanding.

If you want more later, the obvious candidates are a Journey lesson card (the Strategies row now
reads cleanly) and a generated AI report. Both need care about what is in frame — see below.

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
