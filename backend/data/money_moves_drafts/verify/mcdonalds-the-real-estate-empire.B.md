# Verifier B: mcdonalds-the-real-estate-empire.json

**Verdicts (50 claims): SUPPORTED 46 · IMPRECISE 4 · UNSUPPORTED 0 · WRONG 0**

Before I opened the ledger, I rated #12 (Des Plaines 1955) WRONG, which made the count 46 / 3 / 0 / 1. After the ledger, I downloaded and read the 2025 proxy it cites. The proxy supports the 1955 dating, while the company's own FAQ and history page date the founding to 1940. Because the company's sources conflict, #12 is now IMPRECISE. The fix is unchanged. Details are under #12 and in §4.

Sources I downloaded myself, into `SCR/verify/src-B/`:
- **10-K FY2025**: McDonald's Corp. (CIK 63908) Form 10-K for the year ended 2025-12-31, filed 2026-02-24, accession 0000063908-26-000035, `mcd-20251231.htm` (my text conversion is `mcd-20251231.txt`). Page numbers below are the "2025 Annual Report N" page footers.
- **History page**: corporate.mcdonalds.com/corpmcd/our-company/who-we-are/our-history.html, read in the browser pane (curl and WebFetch got 403).
- **US FAQ**: www.mcdonalds.com/us/en-us/faq/when-was-mcdonalds-founded.html, dated January 16, 2026, read in the browser pane.

I wrote sections 1–3 before I opened the writer's ledger. I never opened `SCR/src/`.

## 2. Every claim

| # | Article wording (short) | Verdict | Source + location |
|---|---|---|---|
| 1 | Subtitle: "whose franchise contract is also a lease" | **IMPRECISE** | 10-K p.53 Franchise Arrangements; p.4 Developmental License (details below) |
| 2 | Subtitle: "a large part of what it earns arrives as rent from the operators who run restaurants on property it controls" | SUPPORTED | p.53: Rents $10,442M of total revenues $26,885M (p.39) = 38.8% |
| 3 | cardSubtitle: "a burger brand is also a landlord" | SUPPORTED | p.53 "Conventional franchise arrangements generally include a lease and a license"; p.30 "significant real estate operations" |
| 4 | KH1: conventional franchise, company "generally holds the land and building", operator pays "equipment and fit-out" | SUPPORTED | p.3 Item 1 "generally owns or secures a long-term lease on the land and building … franchisee pays for equipment, signs, seating and décor" |
| 5 | KH2: FY2025 franchise rent ≈ $10.4B, royalties ≈ $6.0B | SUPPORTED | p.53: Rents 10,442; Royalties 6,018 ($M) |
| 6 | KH2 title "Rent Outearns Royalty" | SUPPORTED | p.53 (10,442 > 6,018) |
| 7 | KH3: "When a 20-year franchise ends, the company keeps control of the property and decides what happens next" | **IMPRECISE** | p.53 (details below) |
| 8 | Cover paragraph: roadside restaurant with a drive-through lane, no sign | SUPPORTED (it describes the picture; the only implied company fact is that restaurants have drive-throughs) | p.9 "nearly 29,000 drive thru locations globally" |
| 9 | "In McDonald's own filings, a large part of the business sits underneath both" (the building and the menu) | SUPPORTED | p.30 Risk Factors "We have significant real estate operations"; p.21 land/building ownership |
| 10 | Conventional franchise: company "generally owns or holds a long-term lease on the land and the building" | SUPPORTED | p.3 Item 1 (same passage as #4) |
| 11 | Franchisee pays for "the equipment, the signs, the seating and the décor" | SUPPORTED | p.3 Item 1 (verbatim list) |
| 12 | "McDonald's traces its own beginning to a restaurant in Des Plaines, Illinois, in 1955" | **IMPRECISE** (WRONG before the ledger; revised, see header) | Company history page and US FAQ say 1940; the 2025 proxy says 1955 (details below) |
| 13 | 45,356 restaurants at end of 2025 | SUPPORTED | p.3 "Of the 45,356 McDonald's restaurants at year-end 2025"; p.44 table |
| 14 | "about 95% were franchised" | SUPPORTED | p.3 / p.14 / p.20 "approximately 95%" (43,317 / 45,356 = 95.5%) |
| 15 | "franchised covers two quite different deals, and only one of them makes the company a landlord" | SUPPORTED (see note) | p.3 three structures, "conventional franchise, developmental license or affiliate"; p.4 "developmental license and affiliate arrangements are largely the same" |
| 16 | "Roughly 20,700" run by developmental licensees and affiliates | SUPPORTED | p.44: developmental licensed 9,675 + foreign affiliated 11,072 = 20,747 |
| 17 | They "supply their own capital, including the real estate, and pay a royalty on sales" | SUPPORTED | p.4 "providing capital (including the real estate interest) … it receives a royalty based on a percent of sales" |
| 18 | "About 22,600 ran under conventional franchises" | SUPPORTED | p.44: conventional franchised 22,570 |
| 19 | "the company operated about 2,000 itself" | SUPPORTED | p.44: Company-owned and operated 2,039 |
| 20 | "On those sites it generally controls the property" | SUPPORTED | p.36 Item 2 "generally owns or secures a long-term lease on the land and building for conventional franchised and Company-owned and operated restaurant sites" |
| 21 | Bullet: "A royalty, set as a percent of sales" | SUPPORTED | p.4; p.53 "rent and royalties … based upon a percent of sales" |
| 22 | Bullet: "Rent, also tied to a percent of sales, with a specified minimum" | SUPPORTED | p.4 "rent and royalties based upon a percent of sales, with specified minimum rent payments"; p.45 minimum rent + variable rent |
| 23 | Bullet: "An initial fee when a restaurant opens or a new franchise is granted" | SUPPORTED | p.4 "initial fees paid upon the opening of a new restaurant or grant of a new franchise" |
| 24 | Bullet: "generally, the site's property taxes, insurance and maintenance" | SUPPORTED (see misreading note in §3) | p.53 "Franchisees generally pay related occupancy costs including property taxes, insurance and site maintenance" |
| 25 | Minimum rent is based on the company's investment in owned sites and parallels its own lease payments and escalations on leased sites | SUPPORTED | p.53 "Minimum rent payments are based on the Company's underlying investment in owned sites and parallel the Company's underlying leases and escalations on properties that are leased" (also p.23) |
| 26 | "A franchise generally runs 20 years" | SUPPORTED | p.3 "franchise/license agreements that generally have 20-year terms"; p.53 |
| 27 | At the end, the company keeps the land and building, and can renew with the same operator, pick another, or close | SUPPORTED | p.53 "maintains control of the underlying real estate and building and can either enter into a new 20-year franchise arrangement with the existing franchisee or a different franchisee, or close the restaurant" |
| 28 | FY2025 revenue ≈ $26.9B | SUPPORTED | p.39 Total revenues 26,885 |
| 29 | ≈ $16.5B from franchised restaurants | SUPPORTED | p.39 16,548 |
| 30 | ≈ $9.7B of sales at company-operated restaurants | SUPPORTED | p.39 9,690 |
| 31 | Inside the franchised figure, rent ≈ $10.4B, royalties ≈ $6.0B | SUPPORTED | p.53 |
| 32 | "The landlord line beat the brand line by more than $4 billion" | SUPPORTED | 10,442 − 6,018 = 4,424 |
| 33 | Occupancy costs "include lease expense and depreciation" | SUPPORTED | p.15 "Franchised restaurant occupancy costs include lease expense and depreciation" |
| 34 | Franchised margins ≈ $13.9B | SUPPORTED | p.15 table Franchised Total 13,930 (= 16,548 − 2,618) |
| 35 | "roughly 90% of all restaurant margin dollars" | SUPPORTED | p.15 "Franchised margins represented approximately 90% of restaurant margin dollars" (13,930 / 15,351 = 90.7%) |
| 36 | Owned ≈ 56% of the land and ≈ 80% of the buildings in consolidated markets | SUPPORTED | p.21 "As of December 31, 2025 and 2024, the Company owned approximately 56% of the land and approximately 80% of the buildings for restaurants in its consolidated markets" |
| 37 | Existing conventional franchises carried ≈ $31.5B of future minimum rent | SUPPORTED (see note) | p.53 "Future gross minimum rent payments due … under existing conventional franchise arrangements", Total 31,451 |
| 38 | Callout: a brand that is also the landlord "collects rent with a floor under it, and keeps the site when the contract ends" | SUPPORTED, as applied to McDonald's (a general lesson, not a company claim) | p.53 minimum rent; control at end of term |
| 39 | Much is leased; "leasing the land alone at some sites and the land and building at others" | SUPPORTED | p.54 "lessee in a significant real estate portfolio, primarily through ground leases (… leases the land and generally owns the building) and through improved leases (… leases the land and buildings)"; p.21 owns ~56% of land |
| 40 | Lease liabilities ≈ $14.8B at end-2025, "the present value of the minimum lease payments it expects to make" | SUPPORTED (see note) | p.55 "Present value of lease liability … 14,840"; p.54 current 694 + long-term 14,147 = 14,841; "present value of the Company's estimated future minimum lease payments" |
| 41 | Royalties and the variable part of rent shrink when franchisee sales shrink | SUPPORTED (derived) | p.45 "Royalty revenues are based on a percent of sales … variable rent payments based on a percent of sales" |
| 42 | Filing warns franchised revenue depends largely on franchisees growing sales | SUPPORTED | p.29 Risk Factors "The revenues we realize from franchised restaurants are largely dependent on the ability of our franchisees to grow their sales" |
| 43 | "The model leans on franchisee economics" | SUPPORTED | p.29 "relies to a large degree on the financial success and cooperation of our franchisees" |
| 44 | Relies on operators "willing and able to fund reinvestment" | SUPPORTED | p.29 "relies on the willingness and ability of our franchisees and affiliates to implement major initiatives, which may include financial investment, and to remain aligned with us on … capital-intensive reinvestment plans"; p.3 "Franchisees are responsible for reinvesting capital" |
| 45 | "may co-invest to modernise restaurants" | SUPPORTED | p.3 "the Company may co-invest with franchisees to fund improvements … development of modernized, more attractive and higher revenue generating restaurants" |
| 46 | Long commitments leave "limited flexibility to reshape the portfolio as trade areas and customer habits change" | **IMPRECISE** | p.30 Risk Factors (details below) |
| 47 | "the buildings it holds keep depreciating on its own books" | SUPPORTED | p.45 buildings depreciated straight-line "up to 40 years"; p.53 property D&A $1.6B for 2025 |
| 48 | Stat "~95% Restaurants Franchised, 2025" | SUPPORTED | p.3 / p.20 |
| 49 | Stat "~$10.4B Franchise Rent, Fiscal 2025", trend up | SUPPORTED | p.53 10,442 vs 10,017 in 2024 (+4.2%) |
| 50 | Stat "~56% Land Owned, Consolidated Markets", trend neutral | SUPPORTED | p.21 (same 56% at end-2025 and end-2024, so "neutral" is right) |

### Details for everything not SUPPORTED

**#12 IMPRECISE (WRONG before the ledger): Des Plaines 1955 as "its own beginning".** The company's consumer and corporate history pages date the beginning to 1940 in San Bernardino. They describe Des Plaines as the first restaurant east of the Mississippi, and the first one for the corporation's predecessor.
- US FAQ "When was McDonald's founded?" (2026-01-16): "The McDonald brothers opened the first McDonald's on May 15, 1940 in San Bernadino, California." On 1955 it says the Des Plaines restaurant was "the first McDonald's east of the Mississippi river".
- The corporate history timeline has the heading "1940 - McDonald's Is Founded". It also says the Des Plaines restaurant was "the first restaurant for McDonald's System, Inc., a predecessor of McDonald's Corp. in Des Plaines, Illinois in April, 1955."
- A listener who knows the San Bernardino story will hear the article's sentence as false. The company's own FAQ contradicts it.
- Added after the ledger: the 2025 proxy (DEF 14A, accession 0001558370-25-004544, Chairman's letter; I downloaded it to `src-B/def14a-2025.htm`) supports the article. It says "… opened the first McDonald's in Des Plaines, Illinois, on April 15, 1955 …" and "our 70-year legacy". So the company dates itself both ways. The sentence is defensible on the proxy alone, but it presents one side of the company's own record, and the consumer FAQ answers "When was McDonald's founded?" with 1940.
- **Fix** (no person named): "The first McDonald's opened in San Bernardino, California, in 1940. The corporation itself grew from a restaurant its predecessor opened in Des Plaines, Illinois, in 1955." Or simply cut the sentence; nothing else in the article depends on it.

**#1 IMPRECISE: subtitle "whose franchise contract is also a lease".**
- Only the conventional contract includes a lease, and only "generally". p.53: "Conventional franchise arrangements generally include a lease and a license". p.4: under a developmental license or affiliate arrangement the licensee provides "capital (including the real estate interest)", and the company "receives a royalty based on a percent of sales".
- Developmental and affiliate restaurants are 20,747 of the 43,317 franchised restaurants (48%), and they have no lease. The article's own section 2 says exactly this, so the subtitle contradicts its own body.
- **Fix:** "A burger company whose conventional franchise contract generally includes a lease, so that a large part of its revenue arrives as rent from the operators who run restaurants on property it controls."
- "What it earns" → "its revenue" is optional: rent is 38.8% of revenue, while "earns" can be heard as profit.

**#7 IMPRECISE: KH3 "When a 20-year franchise ends, the company keeps control of the property".**
- The keep-control term belongs to conventional franchises only. p.53: "At the end of the 20-year franchise arrangement, the Company maintains control of the underlying real estate and building and can either enter into a new 20-year franchise arrangement with the existing franchisee or a different franchisee, or close the restaurant."
- A developmental licensee supplies its own real estate interest (p.4). The highlight card stands alone, and without the word "conventional" it covers all 43,317 franchised restaurants.
- **Fix:** "When a conventional franchise, generally 20 years, ends, the company keeps control of the land and building and decides what happens next."

**#46 IMPRECISE: the filing's qualifier "quickly" was dropped.**
- p.30: "As we generally secure long-term real estate interests for our restaurants, we have limited flexibility to quickly alter our real estate portfolio. The competitive business landscape continues to evolve in light of changing business trends, consumer preferences, trade area demographics …"
- The article says "limited flexibility to reshape the portfolio", which reads as limited flexibility at all. The filing says limited flexibility to move quickly.
- **Fix:** "Long real estate commitments leave it limited flexibility to quickly reshape the portfolio as trade areas and customer habits change, while the buildings it holds keep depreciating on its own books."

### Notes on SUPPORTED items (optional tightening, not errors)
- **#15:** The 10-K lists three structures (conventional franchise, developmental license, affiliate) and calls the last two "largely the same". "Two quite different deals" is a fair grouping. If the editor wants zero exposure: "franchised covers three structures, two of them largely the same, and only one makes the company a landlord."
- **#37:** $31.5B is the gross, undiscounted total over all future years. Section 4's $14.8B lease liability is a present value. A listener could net the two figures. Optional: "about $31.5 billion of future minimum rent, before discounting".
- **#40:** The lease liability includes "option periods that are reasonably certain of being exercised" (p.55). p.54 says these are "not currently a future obligation". The wording "payments it expects to make" covers this honestly.

### Arithmetic recomputed (all correct)
- 9,675 + 11,072 = 20,747 → "roughly 20,700" ✓
- 20,747 + 22,570 + 2,039 = 45,356 ✓
- 43,317 / 45,356 = 95.5% → "about 95%" ✓
- 10,442 − 6,018 = 4,424 → "more than $4 billion" ✓
- 16,548 − 2,618 = 13,930 → "about $13.9 billion" ✓
- 13,930 / 15,351 = 90.7% → "roughly 90%" ✓ (the filing also says approximately 90%)
- 694 + 14,147 = 14,841, and the PV table shows 14,840 → "about $14.8 billion" ✓
- 31,451 → "about $31.5 billion" ✓
- Rent / revenue = 10,442 / 26,885 = 38.8% → "a large part" ✓

## 3. Rule-break findings

- **Investment advice, valuation, share price or market cap:** none. The highlight callout ("ask who holds the ground") is a way to read a business, not a recommendation.
- **A person named:** none. Keep it that way in the #12 fix: do not reintroduce the founders' names.
- **Direct quotation of a person:** none. The warning callout paraphrases the 10-K.
- **Time-relative words:** none. I scanned for today, now, currently, recent(ly), this year, latest, still, will.
- **Wording of wrongdoing:** none.
- **Cover paragraph:** it states nothing untrue of the company. A drive-through lane is consistent with "nearly 29,000 drive thru locations" (p.9). I could not see the image itself, so whether the description matches the actual art is not verified.
- **Things a careful reader could misread:**
  - Bullet 4 sits under "How a conventional franchisee pays", so it can be heard as property taxes, insurance and maintenance being paid *to McDonald's*. The filing says only that franchisees "generally pay related occupancy costs". Optional: "And, generally, the site's property taxes, insurance and maintenance, paid by the franchisee directly".
  - "On those sites it generally controls the property" follows a sentence that mentions three groups. It refers correctly to the conventional and company-operated sites, but "those 24,600 or so sites" would remove any doubt.
- **Style nit:** "modernise" is British spelling; the filing says "modernized". Follow the app's house spelling.

## 4. Claims the ledger missed or cited wrongly

The ledger's coverage is complete: every factual sentence in the article maps to an entry (H1–H8, claims 1–40). I spot-checked its page references and found them correct, including p.14 "Total Revenues", p.46 "ground leases", p.21 for the 56%/80% figures, and p.55 for the present-value table. Its excerpts match my own copy of the same 10-K (accession 0000063908-26-000035). Where it falls short, the problem is in how it reads its sources, not in what it covers:

1. **Claim 4 (Des Plaines 1955): the ledger rests on one company source and understates the conflict.**
   - The ledger cites only the 2025 proxy letter. It says McDonald's web pages returned 403, and calls the earlier restaurant something "commonly reported".
   - The company itself reports it. The US FAQ (www.mcdonalds.com/us/en-us/faq/when-was-mcdonalds-founded.html, dated 2026-01-16) says: "The McDonald brothers opened the first McDonald's on May 15, 1940". The corporate history timeline heads its 1940 entry "1940 - McDonald's Is Founded" and describes Des Plaines as "the first McDonald's east of the Mississippi river". I could read both pages in the browser pane even though curl and WebFetch get 403.
   - The proxy's own wording, "opened the first McDonald's in Des Plaines", is contradicted by that FAQ. The ledger should cite both sources, and the article should take the §2 #12 fix.
2. **H1 (subtitle): the cited passage does not support the unqualified wording.** p.53 says "*Conventional* franchise arrangements *generally* include a lease and a license". The subtitle says "whose franchise contract is also a lease", dropping both qualifiers. The ledger quotes the passage but does not flag the gap. 48% of franchised restaurants (20,747 of 43,317, developmental and affiliate) have no lease. Fix: §2 #1.
3. **H5 (third key highlight): same gap.** The cited p.53 passage covers conventional franchise arrangements, but the highlight card says "a 20-year franchise" with no "conventional". Fix: §2 #7.
4. **Claim 39: the ledger's own excerpt shows the dropped word.** The ledger quotes "limited flexibility to *quickly* alter our real estate portfolio", but the article says "limited flexibility to reshape the portfolio". Fix: §2 #46.
5. **Minor coverage gaps (no error, noted for completeness):**
   - Neither "neutral" stat trend has a source. ~95% franchised: 2024 was 41,432 / 43,477 = 95.3% (p.44), so neutral holds. ~56% land owned: the filing gives the same 56% for end-2025 and end-2024 (p.21), so neutral holds.
   - Claim 40 ("buildings … keep depreciating") is better supported by the policy sentence on p.45, "buildings–up to 40 years", straight-line, than by the accumulated-depreciation figure the ledger cites.
