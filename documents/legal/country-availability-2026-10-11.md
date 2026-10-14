# App Store country availability — decision of 2026-10-11

**Owner decision (2026-10-11):** "yes, set it to the 109 countries for me." The app went from United States only
to **110 storefronts** (the US + 109) the same day, through the App Store Connect API. `availableInNewTerritories`
stays **false**, so Apple never adds a country on its own. This file records why the other 65 storefronts are
OFF and what would turn each group on. It is research, not legal advice: the licence-risk group especially deserves
a local lawyer before any marketing there.

Basis: a multi-agent research pass on 2026-10-11 (vendor terms + US sanctions, Apple storefront rules,
financial-advice licensing, privacy/AI law), then a synthesis and two adversarial checks (one tried to refute every
exclusion, one hunted for missed blockers). Re-check the time-sensitive sources before changing anything.

## What did NOT need changing

The two subscriptions and four credit packs were already available in all 175 storefronts with prices everywhere
(USD base, automatic prices), `availableInNewTerritories = true`. A product sells only where the APP is available,
so the 65 excluded storefronts stay closed through the app setting alone.

## OFF — and what would turn each group on

| Group | Storefronts | Why | To turn on |
|---|---|---|---|
| Hard blockers (7) | China mainland, Russia, Belarus, Hong Kong, Macao, Myanmar, Afghanistan | Not Gemini available regions; Gemini terms allow API clients only for users in them ([regions](https://ai.google.dev/gemini-api/docs/available-regions), [terms](https://ai.google.dev/gemini-api/terms)). China also needs an MIIT ICP filing; Russia: no App Store payment processing since 2026-04-01. Hong Kong also SFO Type 4 / s115 ([SFC](https://www.sfc.hk/en/faqs/intermediaries/licensing/Actively-markets-under-section-115-of-the-SFO)). | HK/Macao/Myanmar/Afghanistan: one verifier argued AI calls for these users could go through Vertex AI (Cloud Service Terms carry no end-user country list) — verify before relying on it; HK still has the SFO question. China/Russia: not realistic. |
| Licence risk (14) | United Kingdom, Australia, Japan, Singapore, South Korea, New Zealand, Taiwan, Malaysia, Saudi Arabia, Israel, Thailand, Indonesia, Chile, South Africa | Paid opinions on named securities (fair value, BUY/SELL meter, score, AI reports, chat) are likely a licensed activity even from abroad. Strongest: UK ([FCA FAMR](https://www.fca.org.uk/firms/financial-advice-market-review-famr/changes-regulated-activities-order/printable/print): unauthorised persons "may not provide regulated advice"), Australia (s911A; [ASIC v Scholz](https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2022-releases/22-371mr-federal-court-makes-findings-against-social-media-finfluencer-tyson-scholz)), Singapore ([MAS 2024](https://www.mas.gov.sg/news/parliamentary-replies/2024/pq-on-complaints-against-online-finfluencers)). Thailand/Indonesia are the weakest evidence. | A local licence or representative arrangement (e.g. Australia: Corporate Authorised Representative of an AFSL holder, the Simply Wall St model — needs a company); or a per-storefront "research-off" mode plus a local opinion (UK: PERG 7 / Art 54 certificate). |
| Skip for now (14) | Türkiye, Vietnam, Rwanda, Ukraine; Venezuela, Iraq, Lebanon, Libya, Yemen, Mali, Nicaragua, DR Congo, Republic of the Congo, Zimbabwe | Türkiye: VERBIS registration via a Turkish representative + KVKK transfer notices. Vietnam: PDP Law 91/2025 transfer impact assessment. Rwanda: NCSA registration (storage abroad needs its certificate). Ukraine: occupied Crimea/DNR/LNR are OFAC-embargoed and sit inside the storefront → needs a server geo-IP block first. The other ten are on [CoinGecko's Excluded Countries list](https://support.coingecko.com/hc/en-us/articles/51759637961881-Excluded-Countries-List) (2026-09-24). | Türkiye/Vietnam/Rwanda: the local filing. Ukraine: ship the geo-IP gate. The ten: hide the CoinGecko crypto screens there, or CoinGecko's written OK. |
| Europe (30) | EU-27, Norway, Iceland, Switzerland | EU: selling IAP there very likely makes the owner a DSA **trader** (Apple lists IAP revenue as a factor). Gemini allows only **Paid Services** for EEA/UK/CH users. GDPR Art 27 representative. MAR Art 20 / Reg 2016/958 disclosure on fair value and ratings. | Switch to trader in ASC (publishes an address — a P.O. box is allowed — phone, email); confirm the Gemini key is billed; appoint an EU/EEA representative (~€420–850/yr); ship the disclosure block. Switzerland needs only the Gemini check + a one-page nFADP Art 14 note. |

⚠️ **Correction:** `LAUNCH_CHECKLIST.md` (DSA section, 2026-08-15) says declaring non-trader makes Apple remove
the app from all EU storefronts. Apple's own DSA page (re-read 2026-10-11) says a declared non-trader's EU customers
are only told consumer rights do not apply; the February 2025 removals hit apps with NO declared status. The EU was
closed only because availability was US-only. The trader question still blocks the EU, for the reason above.

## ON (110)

USA, Canada, India, Mexico, Brazil, UAE, Philippines, Argentina and the rest: AGO, AIA, ALB, ARE, ARG, ARM, ATG,
AZE, BEN, BFA, BHR, BHS, BIH, BLZ, BMU, BOL, BRA, BRB, BRN, BTN, BWA, CAN, CIV, CMR, COL, CPV, CRI, CYM, DMA, DOM,
DZA, ECU, EGY, FJI, FSM, GAB, GEO, GHA, GMB, GNB, GRD, GTM, GUY, HND, IND, JAM, JOR, KAZ, KEN, KGZ, KHM, KNA, KWT,
LAO, LBR, LCA, LKA, MAR, MDA, MDG, MDV, MEX, MKD, MNE, MNG, MOZ, MRT, MSR, MUS, MWI, NAM, NER, NGA, NPL, NRU, OMN,
PAK, PAN, PER, PHL, PLW, PNG, PRY, QAT, SEN, SLB, SLE, SLV, SRB, STP, SUR, SWZ, SYC, TCA, TCD, TJK, TKM, TON, TTO,
TUN, TZA, UGA, URY, USA, UZB, VCT, VGB, VUT, XKS, ZMB.

Caution notes for this group (accepted risk, not blockers):
- **Canada** — keep every output non-tailored (NI 31-103 s.8.25 "advising generally"); add a holds/does-not-hold
  line to fair value, meter and score.
- **India** — SEBI Research Analyst rules reach research on India-listed securities, including from abroad; search
  already hides dotted foreign listings, but US-listed ADRs of Indian issuers (INFY, WIT, IBN, HDB, RDY) still get
  opinions.
- **Brazil** — ECA Digital (Lei 15.211/2025) art. 40 requires a Brazilian legal representative IF the app is
  "probable access" for minors; an 18+ finance app probably is not — write that conclusion down.
- **UAE, Kenya, the Gulf states, the rest of Latin America/Africa/Central Asia** — not fully assessed for
  investment-advice licensing. Kenya's Capital Markets Act defines an investment adviser broadly; the Gulf states
  were not checked (Saudi Arabia has acted against an offshore operator).
- **Tax** — Apple collects VAT/GST in only the storefronts on Schedule 2 Exhibit B; elsewhere the developer is
  responsible under local law. Ask a tax adviser about any that matter.
- The live social marketing engine posts worldwide with an App Store link, which weakens any "not targeting
  country X" defence. Marketing posts already carry no opinion on a named ticker (`marketing.md` §1).

## Open items

1. **FMP display licence** — 1,000 unique monthly users; above it, notify FMP and the fee re-tiers (§2.6). Ask
   FMP for the next tier's price, and to confirm in writing the year-one price hold offered in negotiation (it is
   NOT in the signed Special Terms). 2026-10-11: 20 accounts, 28 identities active in 30 days.
2. **Server geo-IP gate** — refuse Crimea/Sevastopol and the DNR/LNR (OFAC embargo), plus Cuba, Iran and North
   Korea (no storefront, but foreign Apple IDs + VPNs reach the API). Syria: its US embargo ended in 2025; keep it
   in the gate only as "not a Gemini region + CoinGecko exclusion". Needed before Ukraine.
3. **Gemini paid tier** — confirm the production key's Cloud project has billing on before any EEA/UK/CH user.
4. **FINRA short-interest terms** — FINRA's Equity Data terms (§2.2 non-commercial, §2.3 no fee for the data)
   may conflict with a paid app, even in the US. Unverified; check which terms the API key falls under.
5. **Re-check quarterly**: Gemini available regions (last updated 2026-04-28), OFAC program list, CoinGecko's
   Excluded Countries list.
