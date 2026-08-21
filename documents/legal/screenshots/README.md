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

These are **raw device captures**, not marketed screenshots. Apple accepts them as-is; most apps
add a caption band and a device frame. Do that pass before uploading if you want it.

## Still to capture

Tracking, Updates, a Learn/Journey card, and — required for the four consumable IAPs — one
**Add Credits** screenshot showing all four packs (§6b). Add Credits needs a signed-in account,
so shoot it with the App Review demo account once it exists.

## ⚠️ Screens to keep OUT of frame (Guideline 5.2.1)

The three Journey lesson titles were renamed on 2026-08-20, but these still put a real person's
name on screen:

- **Whales / 13F tab** — 56 named real investors and 11 sitting politicians
- **Book-cover shelf** — covers typeset real author names
- **Journey screen, investor quote card** — attributed `— Warren Buffett`
  (`InvestorPathModels.swift:428`); it sits below Level 4, so the Journey screen is only safe to
  shoot above that fold
- **SmartMoneyInfoSheet** (`:60`) and **ShareholderBreakdownInfoSheet** (`:250`)
