# Money Moves — sources for the hand-written articles

Where each hand-written Money Moves article's facts come from, so a later edit or a reader's
question can be checked against the same document instead of re-researched. Started 2026-11-01
with the three articles below. The earlier 23 articles predate this file.

**The rule these follow** (`.claude/rules/learn-content.md`, the add-learn-content skill): every
figure and date comes from a primary source — an SEC filing on EDGAR or the company's own
investor-relations, press or history page. Never Financial Modeling Prep (its data is licensed for
authenticated display only, and this JSON is also the marketing content pool), never Wikipedia or
news as the cited source. People appear by role only. No quotations, no valuation verdicts.

**How each article was checked.** One writer built a numbered claim ledger: every claim, its
source location and a verbatim excerpt. Two independent verifiers then audited every claim in
the article. Verifier A audited the ledger, after confirming the writer's downloads were
byte-identical to EDGAR. Verifier B re-sourced everything from scratch and only read the ledger
afterwards. Every correction below was applied before narration. Page numbers are each filing's
own printed page numbers.

⚠️ The articles are narrated. Changing any narrated sentence means re-voicing the clip
(`.claude/skills/add-learn-content/SKILL.md`, "Transcript must match the audio").

---

## McDonald's: The Real Estate Empire

`mcdonalds-the-real-estate-empire` · Blueprints · published 2026-11-01

### Sources
- **[10-K]** McDonald's Corporation, Form 10-K for fiscal 2025 (year ended 2025-12-31), filed
  2026-02-24, accession 0000063908-26-000035 —
  https://www.sec.gov/Archives/edgar/data/63908/000006390826000035/mcd-20251231.htm
- **[FAQ]** McDonald's US FAQ, "When was McDonald's founded?" (dated 2026-01-16) —
  https://www.mcdonalds.com/us/en-us/faq/when-was-mcdonalds-founded.html. Also the corporate
  history timeline, https://corporate.mcdonalds.com/corpmcd/our-company/who-we-are/our-history.html.
  Both block automated fetches and were read in a browser.
- **[PROXY]** 2025 DEF 14A, filed 2025-04-07, accession 0001558370-25-004544. Consulted only;
  see correction 3.

### Key figures (all fiscal 2025, from [10-K] unless noted)
| Article says | Filing figure | Where |
|---|---|---|
| First McDonald's opened in San Bernardino, California, in 1940 | "opened the first McDonald's on May 15, 1940" | [FAQ] |
| 45,356 restaurants, about 95% franchised | 43,317 franchised of 45,356 | Item 1, p. 3 |
| ~20,700 developmental licensee / affiliate; ~22,600 conventional; ~2,000 company-operated | 9,675 + 11,072 = 20,747; 22,570; 2,039 | Item 1, p. 4; Note "Nature of Business", p. 44 |
| Conventional franchise: lease + licence; royalty and rent on % of sales with a minimum; initial fee | "Conventional franchise arrangements generally include a lease and a license…" | Notes, "Franchise Arrangements", p. 53 |
| Minimum rent based on investment in owned sites / parallels own lease payments | | Item 1, "Conventional Franchise", p. 3 |
| Franchise generally 20 years; company keeps control of land and building at the end | | Item 1, p. 3; p. 53 |
| Total revenue ~$26.9B; franchised ~$16.5B; company-operated sales ~$9.7B | $26,885M; $16,548M; $9,690M | Statement of Income, p. 39 |
| Rent ~$10.4B, royalties ~$6.0B (difference > $4B) | $10,442M; $6,018M | p. 53 |
| Franchised margins ~$13.9B, ~90% of restaurant margin | $13,930M of $15,352M | MD&A, "Restaurant Margins", p. 15 |
| Owns ~56% of land, ~80% of buildings (consolidated markets) | | MD&A, "Restaurant Development and Capital Expenditures", p. 21 |
| ~$31.5B future minimum rent, before discounting | $15,843M + $15,608M | p. 53 |
| Lease liabilities ~$14.8B (present value) | $14,840M | "Leasing Arrangements", pp. 54–55 |
| Franchised revenue depends on franchisee sales growth; limited flexibility to quickly alter the real estate portfolio | | Item 1A, pp. 29–30 |

### Verification
Verifier A: 49 supported, 4 imprecise, 0 unsupported, 0 wrong. Verifier B: 46 / 4 / 0 / 0.

### Corrections applied
1. Subtitle: "franchise contract is also a lease … what it earns" became "conventional franchise
   contract generally includes a lease … its revenue". Developmental licences, about 48% of
   franchised restaurants, carry no lease, and the figure is revenue, not profit.
2. Highlight: "a 20-year franchise" became "a 20-year conventional franchise".
3. "Traces its own beginning to … Des Plaines … 1955" became "The first McDonald's opened in
   San Bernardino, California, in 1940." The company's own FAQ and history timeline date the
   founding to 1940; only the proxy letter dates it to 1955.
4. "Where the restaurant profit sits" became "where most of the restaurant margin sits" (~90%;
   the filing's term is margin).
5. "Limited flexibility to reshape" became "to quickly reshape" (the filing's wording, p. 30).
6. The $31.5B of future rent is marked "before any discounting", so it is not netted against the
   $14.8B present value.
7. Property taxes, insurance and maintenance are "paid by the franchisee".
8. The cover sentence was rewritten to describe the generated artwork.

---

## Coca-Cola vs. PepsiCo

`coca-cola-vs-pepsico` · Battles · published 2026-11-01

### Sources
- **[KO]** The Coca-Cola Company, Form 10-K for fiscal 2025, filed 2026-02-20, accession
  0001628280-26-010047 —
  https://www.sec.gov/Archives/edgar/data/21344/000162828026010047/ko-20251231.htm
- **[PEP]** PepsiCo, Inc., Form 10-K for fiscal 2025 (year ended 2025-12-27), filed 2026-02-03,
  accession 0000077476-26-000007 —
  https://www.sec.gov/Archives/edgar/data/77476/000007747626000007/pep-20251227.htm
- **[KO-1886]** Coca-Cola history page, "Coca-Cola History" —
  https://www.coca-colacompany.com/about-us/history/the-birth-of-a-refreshing-idea
- **[KO-1985]** Coca-Cola history page, "New Coke: The Most Memorable Marketing Blunder Ever?" —
  https://www.coca-colacompany.com/about-us/history/new-coke-the-most-memorable-marketing-blunder-ever
- **[PEP-1965]** PepsiCo newsroom story, "How Lay's grew from a small business into a global
  brand" (2026-07-03) —
  https://www.pepsico.com/newsroom/stories/2026/how-lays-grew-from-a-small-business-into-a-global-brand.
  PepsiCo's older history URLs return 404.

### Key figures (fiscal 2025)
| Article says | Where |
|---|---|
| Coca-Cola began in Atlanta in 1886 as a syrup | [KO-1886]; [KO] Item 1, p. 2 |
| Two lines of business: concentrate operations and finished product operations; finished products generally bring higher revenue but lower gross margins | [KO] Item 1 |
| Bottlers generally buy concentrate from the Company or Company-authorized suppliers and sell in assigned territories | [KO] Item 1 |
| Five largest bottlers: 44% of worldwide unit case volume | [KO] Item 1, p. 5 |
| Revenue ~$47.9B; concentrate 59% of revenue and 85% of unit case volume (finished 41% / 15%) | [KO] Item 7, p. 37 tables |
| Refranchised bottling in parts of India; agreed to sell part of its stake in African bottling, keeping 25% | [KO] Note 2, p. 73 |
| Equity-method investees (incl. several large bottlers) combined revenue ~$102.8B | [KO] Note 6, p. 85 |
| ~65,900 employees vs PepsiCo ~306,000 | [KO] / [PEP] Item 1, Human Capital, p. 10 |
| April 1985 formula change, first in 99 years, after taste tests of nearly 200,000 consumers; original back 79 days later as Coca-Cola classic | [KO-1985] |
| PepsiCo formed in 1965 when Frito-Lay merged with Pepsi-Cola | [PEP-1965] |
| Net revenue ~$93.9B; convenient foods 58%, beverages 42%; company-owned bottlers 36% of revenue; six reportable segments | [PEP] Note 1, p. 67 |
| DSD systems run by PepsiCo, independent bottlers and distributors; products merchandised by employees or bottlers | [PEP] Item 1 |

### Verification
Verifier A: 45 supported, 10 imprecise, 0 unsupported, 1 wrong. Verifier B: 49 / 5 / 0 / 1.

### Corrections applied
1. **Wrong, found by both verifiers:** "one route into stores for drinks and foods". PepsiCo's
   10-K (Item 7, p. 34) says it is only "carefully evaluating an integrated model" for North
   American food and beverage go-to-market. Rewritten as "a direct hand in how its drinks and
   foods are stocked and displayed on store shelves".
2. "Near-identical colas", "near-twins", "almost the same product" and the tag "SAME DRINK" had
   no source; only the shared recipe type is sourced. They became "made the same basic way",
   and the tag became "ONE RIVALRY, TWO MODELS".
3. "Opposite" business models became "very different". Coca-Cola's finished-drink operations
   are 41% of its revenue, and both companies bottle and sell concentrate.
4. The filings' "generally" was restored on the finished-drink margin trade-off and on the
   bottler's agreements ("or a supplier it authorizes").
5. Added the 15% of volume / 41% of revenue split, so "finished drinks bring in more revenue"
   no longer sounds like it contradicts "concentrate is 59% of revenue".
6. Nigeria was dropped from the refranchising examples (the filing puts it in EMEA). The Africa
   deal is now "part of its stake … keeping 25%".
7. The 1985 callout was re-paraphrased (the draft repeated the company page's wording), and
   "how much rests on the name" became "how strongly drinkers are attached to the brand".
8. Time-relative wording removed: "keeps selling" became "has repeatedly sold", "still owns"
   became "also owns". "Its bottlers" became "its independent bottlers".

Left out as unsourced: the 1899 $1 bottling-rights sale (secondary sites only).

---

## Kodak: Inventing the Future It Couldn't Sell

`kodak-inventing-the-future-it-couldnt-sell` · Value Traps · published 2026-11-01. No
wrongdoing angle; never a claim about the stock.

### Sources (Eastman Kodak Company, CIK 31235)
| Key | Document | Filed |
|---|---|---|
| MIL | Kodak "Milestones" page — https://www.kodak.com/en/company/page/milestones | accessed 2026-11-01 |
| K96 | 10-K fiscal 1996, accession 0000031235-97-000001 | 1997-03-14 |
| K98 | 10-K fiscal 1998, accession 0000031235-99-000001 (1996 comparative gross profit) | 1999-03-10 |
| K03 | 10-K fiscal 2003, accession 0000031235-04-000049 | 2004-03-15 |
| K05 | 10-K fiscal 2005, accession 0001206774-06-000357 | 2006-03-02 |
| 8-K | Chapter 11, accession 0001193125-12-016443 | 2012-01-19 |
| 8-K | Exit of dedicated capture devices business, accession 0000031235-12-000030 | 2012-02-13 |
| 8-K | Patent sale and licensing, accessions 0001193125-12-507443 / -513381 | 2012-12-19 / 12-21 |
| 8-K | Emergence + spin-off to the U.K. pension plan, accession 0001193125-13-355259 (Ex. 99.1) | 2013-09-03 |
| K25 | 10-K fiscal 2025, accession 0001193125-26-104214 | 2026-03-12 |

EDGAR paths: `https://www.sec.gov/Archives/edgar/data/31235/<accession without dashes>/`.

### Key figures
| Article says | Where |
|---|---|
| 1975 prototype: toaster-sized, black-and-white, 10,000 pixels; "what the company calls the world's first digital camera" | MIL |
| Amateur photography needs a camera, film and photofinishing (chemicals, paper); Kodak supplied all of it | K96, Item 1 |
| Fiscal 1996 sales ~$16.0B, gross profit ~$7.6B (~48%) | K96 ($15,968M sales); K98 1996 column ($7,641M) |
| Digital substitution hit film and paper sales and processing in the U.S., Japan and Western Europe; U.S. consumer film volumes expected to fall 10–12% in 2004; rapid digital price declines | K03, MD&A |
| Professional digital camera system 1991; EasyShare 2001; 100 millionth digital still camera 2011 | MIL |
| 2003 strategy: manage traditional for cash, grow digital faster (among other goals) | K03 |
| 2005: digital $7,757M > traditional $6,511M; sales ~$14.3B; gross margin 25.6% vs 29.1%; consumer digital cameras named first in price/mix declines; acquisitions added $1,562M | K05, MD&A |
| 2005 COGS included $391M accelerated depreciation + $37M inventory write-downs from restructuring (~$430M) | K05, restructuring note |
| Chapter 11 Jan 2012; exit from digital cameras, pocket video cameras and digital picture frames (Feb 2012); patents sold and licensed in deals worth ~$525M (Dec 2012); emerged Sept 2013, completing the spin-off the same day | 8-Ks above |
| Fiscal 2025 revenue ~$1.1B ($1,069M); segments Print, Advanced Materials and Chemicals, Brand; plates a recurring revenue stream, ~half of revenue (52%) | K25, Item 1 and segment note |

### Verification
Verifier A: 43 supported, 8 imprecise, 0 unsupported, 0 wrong. Verifier B: 42 / 5 / 0 / 0.

### Corrections applied
1. "Sales shifted toward hardware sold once" became "from film toward lower-margin digital
   products, led by cameras under steady price pressure". 2005 digital sales included acquired
   printing (KPG, Creo) and health businesses, and the filings' own words are "higher margin
   traditional … lower margin digital".
2. The caveat now notes the ~$430M of restructuring charges inside 2005 cost of goods sold
   (about 3 margin points; 1996 restructuring sat outside it). The 2005 crossover is marked as
   "swelled by acquired printing businesses".
3. "Sales Up, Margin Down" became "Digital Up, Margin Down".
4. Subtitle: "whose profits came from … the very things that camera made unnecessary" became
   "whose richest margins came from … the sales digital substitution hit hardest". No filing
   splits profit by product, and the FY2003 10-K says digital was "adding to the consumption of
   film and paper".
5. Patents: "sell … for about $525 million" became "sell and license … in deals worth about
   $525 million".
6. The 2003 strategy is described as "among other goals" (it had four elements). "The threat
   came from inside" became "was first built inside the company". "It invented it" became "it
   had a working prototype of it in 1975". The card subtitle no longer says "the digital
   camera".
7. Restored the filing's scope, "in the U.S., Japan and Western Europe".
8. "Stop making digital cameras" became "exit its business of …" (8-K: "dedicated capture
   devices business"). The spin-off happened on emergence day, not before it.
9. "Still, industrial and motion picture film" became "photographic, …" ("still" is heard as a
   time word when narrated). The cover sentence was rewritten to match the artwork.

Left out as unsourced: "a young engineer" (Kodak says "researcher"), the inventor's age, the
first image's subject, the 1978 patent and the "management shelved it" story (news and
encyclopedia only). Pre-1993 "peak sales" were left out because they included Eastman Chemical.
