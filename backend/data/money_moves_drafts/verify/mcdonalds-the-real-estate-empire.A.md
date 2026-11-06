# Verifier A: "McDonald's: The Real Estate Empire" (`mcdonalds-the-real-estate-empire.json`)

**Verdicts: 49 SUPPORTED / 4 IMPRECISE / 0 UNSUPPORTED / 0 WRONG** (53 claims; the cover-picture paragraph is listed separately because it makes no claim about the company).

## 0. Are the writer's source copies genuine?

- **I fetched the index myself.** `https://data.sec.gov/submissions/CIK0000063908.json` lists `10-K 0000063908-26-000035`, filed 2026-02-24, report date 2025-12-31, primary document `mcd-20251231.htm`. It also lists `DEF 14A 0001558370-25-004544`, filed 2025-04-07, meeting 2025-05-20, document `mcd-20250520xdef14a.htm`. Both match the ledger. (A newer proxy exists, 0001193125-26-145548, filed 2026-04-07. The writer used the 2025 one, which is fine for the one fact it supports.)
- **I downloaded both documents myself** from `www.sec.gov/Archives/edgar/data/63908/…` into `verify/src-A/`. Their SHA-256 hashes are byte-identical to the writer's copies:
  - 10-K: `35ddcbf4…cadfc2`
  - proxy: `af150992…625149`
- **I converted my own copies to text with my own parser.** I found every ledger excerpt I checked in that text: well over five, about 20 passages across Items 1, 1A and 2, MD&A, the income statement and the notes.
- **Page numbers.** I checked the ledger's page numbers against the filing's "McDonald's Corporation 2025 Annual Report N" footers: p. 3, 4, 15, 21, 23, 29, 30, 36, 39, 44, 45, 53, 54 and 55. All are correct.

All fiscal-year figures below are fiscal 2025 (year ended December 31, 2025) from the 10-K unless stated otherwise.

## 1. Every claim

### Header strings

| # | Article wording (short) | Verdict | Source + location | Passage / fix |
|---|---|---|---|---|
| H1 | subtitle: "whose franchise contract is also a lease, so that a large part of what it earns arrives as rent … on property it controls" | **IMPRECISE** | 10-K Franchise Arrangements p. 53; Nature of Business table p. 44; income statement p. 39 | See the H1 note below the table. |
| H2 | cardSubtitle: "a burger brand is also a landlord" | SUPPORTED | p. 53: "rental income (lease), royalty income (non-lease)" | |
| H3 | title "The Real Estate Empire" / tag "THE LANDLORD MODEL" (framing) | SUPPORTED | Item 1A p. 30: "We have significant real estate operations, primarily in connection with our restaurant business." | |
| KH0 | "On a conventional franchise, the company generally holds the land and building. The operator pays for the equipment and fit-out." | SUPPORTED | Item 1 p. 3: "the Company generally owns or secures a long-term lease on the land and building … the franchisee pays for equipment, signs, seating and décor." | |
| KH1 | "In fiscal 2025, franchise rent … about $10.4 billion, royalties … about $6.0 billion." | SUPPORTED | p. 53 table: Rents 10,442; Royalties 6,018 (2025 column, $M) | |
| KH2 | "When a 20-year franchise ends, the company keeps control of the property and decides what happens next." | **IMPRECISE** (minor) | p. 53, inside the conventional-franchise paragraph: "At the end of the 20-year franchise arrangement, the Company maintains control of the underlying real estate and building …" | See the KH2 note below the table. |
| S1 | "~95%" Restaurants Franchised, 2025 | SUPPORTED | Item 1 p. 3: "Of the 45,356 McDonald's restaurants at year-end 2025, approximately 95% were franchised." (43,317 / 45,356 = 95.5%) | |
| S2 | "~$10.4B" Franchise Rent, Fiscal 2025, trend up | SUPPORTED | p. 53: Rents 10,442 (2025) vs 10,017 (2024), so "up" is right | |
| S3 | "~56%" Land Owned, Consolidated Markets, neutral | SUPPORTED | MD&A p. 21: "As of December 31, 2025 and 2024, the Company owned approximately 56% of the land …". Same figure both years, so "neutral" is right. | |

**H1 note.** The article itself says only one of the two franchised deals includes a lease. 20,747 of the 43,317 franchised restaurants (about 48%) are developmental-licensed or affiliated, and pay "a royalty … and generally pay initial fees" with no rent (p. 53). Their capital includes "the real estate interest" (p. 4). So "whose franchise contract is also a lease" overgeneralises. It also drops the filing's "generally": "Conventional franchise arrangements generally include a lease and a license". And "what it earns" can be heard as earnings (profit), while the figure is revenue: rents are $10,442M of $26,885M, about 39%.

**Fix:** "A burger company whose conventional franchise contract is also a lease, so that a large part of its revenue arrives as rent from the operators who run restaurants on property it controls."

**KH2 note.** Licence agreements also "generally have 20-year terms" (p. 3), but under them the licensee supplies the real estate (p. 4). A standalone card that says "a 20-year franchise" can be read as covering every franchised restaurant.

**Fix:** "When a 20-year conventional franchise ends, the company keeps control of the property and decides what happens next."

### Section 1, "A Building on a Plot"

| # | Article wording | Verdict | Source + location | Passage / fix |
|---|---|---|---|---|
| — | Cover paragraph ("The object above is a blueprint … no sign anywhere, and that absence is the point.") | N/A, not counted | describes the illustration | Says nothing untrue of the company. A drive-through is consistent with the 10-K (p. 30 lists "drive thru"). "No sign" ties to the franchisee paying for signs (p. 3). I cannot see the image, so I did not check that the description matches the artwork. |
| 1 | "In McDonald's own filings, a large part of the business sits underneath both." | SUPPORTED (interpretive) | Item 1A p. 30: "We have significant real estate operations …"; rents are 38.8% of total revenues (p. 53, p. 39) | |
| 2 | "Under a conventional franchise, the company generally owns or holds a long-term lease on the land and the building." | SUPPORTED | Item 1 p. 3 (quoted at KH0) | |
| 3 | "The franchisee pays for the equipment, the signs, the seating and the décor." | SUPPORTED | Item 1 p. 3 | |

### Section 2, "Two Kinds of Franchise"

| # | Article wording | Verdict | Source + location | Passage / fix |
|---|---|---|---|---|
| 4 | "McDonald's traces its own beginning to a restaurant in Des Plaines, Illinois, in 1955." | SUPPORTED | 2025 DEF 14A, Chairman's letter: "…opened the first McDonald's in Des Plaines, Illinois, on April 15, 1955 …"; "our 70-year legacy" | The article rightly avoids "first McDonald's ever" and does not name the founder. |
| 5 | "By the end of 2025 there were 45,356 McDonald's restaurants" | SUPPORTED | p. 3; p. 44 "Total Systemwide restaurants 45,356" | |
| 6 | "about 95% were franchised" | SUPPORTED | p. 3 | |
| 7 | "franchised covers two quite different deals" | SUPPORTED | p. 3: three structures, "conventional franchise, developmental license or affiliate"; p. 4: "developmental license and affiliate arrangements are largely the same" | |
| 8 | "only one of them makes the company a landlord" | SUPPORTED | Rev. recognition p. 45: conventional = "rent and royalties … with minimum rent payments, and initial fees"; licensees and affiliates = "a royalty based on a percent of sales" (no rent) | |
| 9 | "Roughly 20,700 restaurants were run by developmental licensees and affiliates" | SUPPORTED | p. 44: Developmental licensed 9,675 + Foreign affiliated 11,072 = 20,747 | |
| 10 | "who supply their own capital, including the real estate" | SUPPORTED | p. 4: "providing capital (including the real estate interest) … The Company generally does not invest any restaurant capital" | |
| 11 | "and pay a royalty on sales" | SUPPORTED | p. 4: "it receives a royalty based on a percent of sales" | |
| 12 | "About 22,600 ran under conventional franchises" | SUPPORTED | p. 44: 22,570 | |
| 13 | "the company operated about 2,000 itself" | SUPPORTED | p. 44: 2,039 | |
| 14 | "On those sites it generally controls the property." | SUPPORTED | Item 2 p. 36: "generally owns or secures a long-term lease on the land and building for conventional franchised and Company-owned and operated restaurant sites" | |
| 15 | Bullet: "A royalty, set as a percent of sales" | SUPPORTED | p. 4; p. 45 | |
| 16 | Bullet: "Rent, also tied to a percent of sales, with a specified minimum" | SUPPORTED | p. 4: "rent and royalties based upon a percent of sales, with specified minimum rent payments"; p. 45 | |
| 17 | Bullet: "An initial fee when a restaurant opens or a new franchise is granted" | SUPPORTED | p. 4: "initial fees paid upon the opening of a new restaurant or grant of a new franchise" | |
| 18 | Bullet: "And, generally, the site's property taxes, insurance and maintenance" | SUPPORTED | p. 53: "Franchisees generally pay related occupancy costs including property taxes, insurance and site maintenance." | |
| 19 | "based on the company's investment in sites it owns, and parallels its own lease payments and escalations on sites it leases" | SUPPORTED | p. 53: "Minimum rent payments are based on the Company's underlying investment in owned sites and parallel the Company's underlying leases and escalations on properties that are leased." (p. 23: "…lease obligations and escalations") | |
| 20 | "A franchise generally runs 20 years." | SUPPORTED | p. 53: "generally for a period of 20 years"; p. 3 | |
| 21 | "keeps control of the land and building, and can renew with the same operator, choose a different one, or close" | SUPPORTED | p. 53 (quoted at KH2): "…can either enter into a new 20-year franchise arrangement with the existing franchisee or a different franchisee, or close the restaurant." | |

### Section 3, "Rent Is the Bigger Line"

| # | Article wording | Verdict | Source + location | Passage / fix |
|---|---|---|---|---|
| 22 | "In fiscal 2025 McDonald's took in about $26.9 billion." | SUPPORTED | Income statement p. 39: Total revenues 26,885 | |
| 23 | "About $16.5 billion came from franchised restaurants" | SUPPORTED | p. 39: 16,548 | |
| 24 | "about $9.7 billion of sales at restaurants it ran itself" | SUPPORTED | p. 39: 9,690 | |
| 25 | "rent was about $10.4 billion and royalties about $6.0 billion" | SUPPORTED | p. 53: 10,442 / 6,018 | |
| 26 | "The landlord line beat the brand line by more than $4 billion." | SUPPORTED | 10,442 − 6,018 = 4,424 | |
| 27 | "The franchise side is also where the restaurant profit sits." | **IMPRECISE** (minor) | MD&A p. 15: "Franchised margins represented approximately 90% of restaurant margin dollars." Item 1A p. 29: "Our restaurant margins arise from two sources: fees from franchised restaurants … and, to a lesser degree, sales from Company-owned and operated restaurants." | See the claim 27 note below the table. |
| 28 | "After occupancy costs, which include lease expense and depreciation" | SUPPORTED | p. 15: "Franchised restaurant occupancy costs include lease expense and depreciation" | |
| 29 | "franchised margins came to about $13.9 billion" | SUPPORTED | p. 15: Franchised total 13,930 (= 16,548 − 2,618 occupancy, p. 39) | |
| 30 | "roughly 90% of all restaurant margin dollars" | SUPPORTED | p. 15 (above); computed 13,930 / (13,930 + 1,422) = 90.7% | |
| 31 | "Behind them is a large property book." | SUPPORTED | p. 53: "net property and equipment under franchise arrangements totaled $22.8 billion (including land of $7.1 billion)" | |
| 32 | "owned about 56% of the land and about 80% of the buildings for restaurants in its consolidated markets" | SUPPORTED | MD&A p. 21 (quoted at S3) | |
| 33 | "existing conventional franchises carried about $31.5 billion of future minimum rent" | SUPPORTED | p. 53: "Future gross minimum rent payments due to the Company under existing conventional franchise arrangements … Total minimum payments … $31,451" | |
| 34 | Callout: "ask who holds the ground … A brand that is also the landlord collects rent with a floor under it, and keeps the site when the contract ends." | SUPPORTED (editorial lesson) | The McDonald's-specific parts rest on p. 4 (royalty only for licensees), p. 4 / p. 45 (minimum rent) and p. 53 (control at term end) | |

**Claim 27 note.** The sentence says "the restaurant profit", implying all of it, while the next sentence gives about 90%. The filing's measure is restaurant margin, not profit.

**Fix:** "The franchise side is also where most of the restaurant margin sits."

### Section 4, "What the Landlord Carries"

| # | Article wording | Verdict | Source + location | Passage / fix |
|---|---|---|---|---|
| 35 | "Much of it is leased rather than owned" | SUPPORTED | p. 54: "The Company is the lessee in a significant real estate portfolio"; 56% of land and 80% of buildings owned (p. 21), the rest generally leased (p. 36) | |
| 36 | "a tenant across a large portfolio, leasing the land alone at some sites and the land and building at others" | SUPPORTED | p. 54: "primarily through ground leases (the Company leases the land and generally owns the building) and through improved leases (the Company leases the land and buildings)" | |
| 37 | "At the end of 2025 its lease liabilities stood at about $14.8 billion" | SUPPORTED | p. 55: "Present value of lease liability … $14,840"; p. 54: 694 + 14,147 = 14,841 | |
| 38 | "the present value of the minimum lease payments it expects to make" | SUPPORTED | p. 54: "reflect the present value of the Company's estimated future minimum lease payments over the lease term, which includes options that are reasonably certain of being exercised" | |
| 39 | "Royalties and the variable part of rent shrink when franchisee sales shrink" | SUPPORTED (follows from the definitions) | p. 45: royalties "based on a percent of sales"; "variable rent payments based on a percent of sales" | |
| 40 | "the filing itself warns that its franchised revenue depends largely on franchisees growing their sales" | SUPPORTED | Item 1A p. 29: "The revenues we realize from franchised restaurants are largely dependent on the ability of our franchisees to grow their sales." | |
| 41 | "relies on operators willing and able to fund reinvestment" | SUPPORTED | p. 29: "relies on the willingness and ability of our franchisees and affiliates to implement major initiatives, which may include financial investment, and to remain aligned with us on … capital-intensive reinvestment plans"; p. 3: "Franchisees are responsible for reinvesting capital" | |
| 42 | "and may co-invest to modernise restaurants" | SUPPORTED | p. 3: "the Company may co-invest with franchisees to fund improvements … through the development of modernized, more attractive and higher revenue generating restaurants" | |
| 43 | "Long real estate commitments leave it limited flexibility to reshape the portfolio as trade areas and customer habits change" | **IMPRECISE** | Item 1A p. 30: "As we generally secure long-term real estate interests for our restaurants, we have limited flexibility to quickly alter our real estate portfolio. The competitive business landscape continues to evolve in light of changing … consumer preferences, trade area demographics …" | See the claim 43 note below the table. |
| 44 | "the buildings it holds keep depreciating on its own books" | SUPPORTED | Property and Equipment policy p. 45: "depreciation … using the straight-line method over the following estimated useful lives: buildings–up to 40 years"; p. 15, p. 53 | The ledger cites p. 15 and p. 53. p. 45 is the direct source. |

**Claim 43 note.** The filing limits the constraint to *quick* change. The article drops "quickly", which overstates it. The filing also says "real estate interests" (which includes owned sites), not "commitments".

**Fix:** "Long-term real estate interests leave it limited flexibility to quickly reshape the portfolio as trade areas and customer habits change, …"

### Derived arithmetic (all recomputed)

| Calculation | Result | Article says |
|---|---|---|
| 9,675 + 11,072 | 20,747 | "roughly 20,700" ✓ |
| 22,570 + 20,747 + 2,039 | 45,356 | matches the total ✓ |
| 43,317 / 45,356 | 95.5% | "about 95%" ✓ |
| 10,442 − 6,018 | 4,424 | "more than $4 billion" ✓ |
| 10,442 / 26,885 | 38.8% | "a large part" ✓ |
| 16,548 − 2,618 | 13,930 | ✓ |
| 13,930 / 15,352 | 90.7% | "roughly 90%" ✓ |
| 15,843 + 15,608 | 31,451 | ✓ |
| 694 + 14,147 | 14,841 (table: 14,840) | ✓ |

Every rounding is honest: 26.9 / 16.5 / 9.7 / 10.4 / 6.0 / 13.9 / 31.5 / 14.8.

## 2. Rule-break findings

None. The checker (`check_article.py`) prints CLEAN: 660 body words, 13 blocks.

- **Names and quotations.** No person is named (the proxy names the founder; the article does not). There are no quotation marks and no direct quotes.
- **Advice and valuation.** There is no advice, valuation verdict, share price or market cap.
- **Wrongdoing.** There is no wording of wrongdoing.
- **Time-relative words.** None. "Decides what happens next" (KH2) uses "next" relative to the end of the contract, not the present day.
- **Cover paragraph.** It makes no false factual claim about the company.

## 3. Readings a careful reader could take the wrong way (optional fixes, not counted)

- **The rent/royalty split is partly McDonald's own allocation.** The figures behind "Rent Outearns Royalty" and "the landlord line beat the brand line" come from an allocation of one contract's payments. p. 53: "McDonald's has elected to allocate consideration in the franchise contract among lease and non-lease components in the same manner that it has historically … based on a mix of both observable and estimated standalone selling prices." Optional: "Inside the franchised figure, as the company splits it, rent was about $10.4 billion …"
- **The revenue lines do not add up to the total as written.** $16.5B + $9.7B = $26.2B, not the $26.9B total. The gap is $647M of "Other revenues" (technology-platform fees paid by franchisees, and brand licensing; p. 39, p. 3). The article does not claim the lines sum, but a listener may try. Optional: add "plus about $0.6 billion of other fees".
- **The franchisee-payments list is not complete.** "How a conventional franchisee pays" omits the technology and digital-platform fees franchisees pay (p. 3, Other revenues). This is fine as long as the list is not presented as complete.
- **$31.5B and $14.8B are not comparable.** $31.5B (section 3) is gross and undiscounted, covering existing franchise terms. $14.8B (section 4) is a present value and includes reasonably-certain renewal options. Netting one against the other would mislead. The article does not net them. Optional: "about $31.5 billion of future minimum rent, before any discounting".

## 4. Claims the ledger missed

None of substance. The ledger covers every factual sentence (H1–H8 and claims 1–40). Two small points:
- It gives no row for the title or tag framing ("The Real Estate Empire"). That framing is supported by Item 1A p. 30.
- For the depreciation claim (ledger claim 40), it cites the occupancy-cost and net P&E passages rather than the direct Property and Equipment policy (p. 45: buildings depreciated over up to 40 years).
