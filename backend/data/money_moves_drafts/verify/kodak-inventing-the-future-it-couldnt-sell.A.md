# Verifier A: "Kodak: Inventing the Future It Couldn't Sell"

**Verdict counts (51 factual claims): SUPPORTED 43 · IMPRECISE 8 · UNSUPPORTED 0 · WRONG 0.**
Five more strings are opinion, framing or the image description. They are marked N/A and are not counted.

## 0. Source authenticity: done before using the writer's copies

- **EDGAR index.** I fetched it myself: `data.sec.gov/submissions/CIK0000031235.json` plus `CIK0000031235-submissions-001.json`, and EDGAR browse through WebFetch. Every cited accession exists under CIK 31235 with the stated form, filing date, period and primary document:
  - 10-K FY1996: 0000031235-97-000001, filed 1997-03-14
  - 10-K FY1998: 0000031235-99-000001, filed 1999-03-10
  - 10-K FY1999: 0000031235-00-000004, filed 2000-03-13
  - 10-K FY2003: 0000031235-04-000049, filed 2004-03-15, `ek10-k2003.txt`
  - 10-K FY2005: 0001206774-06-000357, filed 2006-03-02, `ek111562.htm`
  - 10-K FY2025: 0001193125-26-104214, filed 2026-03-12, `kodk-20251231.htm`
  - 8-K 0001193125-12-016443, filed 2012-01-19, items 1.03/2.04/9.01
  - 8-K 0000031235-12-000030, filed 2012-02-13, items 2.05/9.01, `exitofcapturedevicesbusiness.htm` + `exhibit99.htm`
  - 8-K 0001193125-12-507443, filed 2012-12-19, item 8.01
  - 8-K 0001193125-12-513381, filed 2012-12-21, items 1.01/8.01
  - 8-K 0001193125-13-355259, filed 2013-09-03, items 3.03/9.01 + `d592194dex991.htm`
- **The documents themselves.** I downloaded all 13 SEC documents myself into `SCR/verify/src-A-kodak/`. Every one is **byte-identical (SHA-256)** to the writer's copy in `SCR/src/kodak-inventing-the-future-it-couldnt-sell/`.
- **Milestones page.** I re-fetched https://www.kodak.com/en/company/page/milestones. It was HTTP 200 and byte-identical to the writer's `kodak_milestones.html`.
- **Excerpts.** I checked about 30 ledger excerpts against my own copies, converted to text with my own script, well over the six required. Every one appears verbatim, apart from whitespace and line breaks: K96 ×4, K98 ×2, K99, K03 ×6, K05 ×6, all five 8-Ks, K25 ×8 and MIL ×7.
- **Fiscal-year attribution.** All correct:
  - FY1996 sales and gross profit come from the 1996 column of the FY1998 10-K income statement, cross-checked against the FY1996 10-K's own statement.
  - FY2005 and FY2004 margins come from the FY2005 10-K MD&A "2005 compared with 2004".
  - The FY2025 revenue is the 2025 column.

## 1. Every claim

Unless noted, MIL = Kodak milestones page; K96/K98/K03/K05/K25 = the 10-Ks above; 8-K dates as listed.

| # | Article wording (short) | Verdict | Source + location |
|---|---|---|---|
| 1 | Title "Inventing the Future It Couldn't Sell" | N/A (headline) | See §5: misread risk |
| 2 | Subtitle: "own researcher built a digital camera in 1975" | SUPPORTED | MIL 1975: "Kodak invented the world's first digital camera"; MIL 2010: "Retired Kodak researcher … invention of the digital camera in 1975" |
| 3 | Subtitle: "whose profits came from film, paper, chemicals and processing" | **IMPRECISE** | See note A |
| 4 | Subtitle: "the very things that camera made unnecessary" | SUPPORTED (interpretive) | K03 Item 1 Photography, Marketing & Competition: substitution "has had an impact primarily on the Company's film and paper sales, and processing services" |
| 5 | Card: "It built the digital camera, but film paid the bills." | SUPPORTED | MIL 1975 (Kodak's own claim). K03 MD&A Photography GP / K05 D&FIS GP 2004: "decrease in sales of higher margin traditional products … lower margin digital products" |
| 6 | HL1: researcher, prototype, 1975, "about the size of a toaster" | SUPPORTED | MIL 1975: "The prototype was the size of a toaster" |
| 7 | HL2: film, paper, chemicals and processing "could earn again each time someone took a photo" | SUPPORTED (mechanism) | K96 Item 1, Consumer Imaging: "requires, at a minimum, a camera, film and photofinishing … chemicals and paper for prints" |
| 8 | HL3 title "Sales Up, Margin Down" | **IMPRECISE** | See note B |
| 9 | HL3: digital revenue overtook traditional in 2005; margin fell to 25.6% | SUPPORTED | K05 Item 1: "For the year ended December 31, 2005, Kodak achieved a level of digital revenues that exceeded traditional revenues." K05 MD&A: "25.6% in the current year as compared with 29.1%" |
| 10 | Stat "1975, Digital Camera Prototype" | SUPPORTED | MIL 1975 |
| 11 | Stat "~48%, Gross Margin, Fiscal 1996" | SUPPORTED | K98 income statement, 1996 column: Sales 15,968; Gross profit 7,641, which is 47.85% |
| 12 | Stat "25.6%, Gross Margin, Fiscal 2005" | SUPPORTED | K05 MD&A, Gross Profit 2005 vs 2004 |
| 13 | Cover: "a photographic print of film: one 35mm roll …" | N/A (describes the image, which I cannot see) | Says nothing untrue about the company |
| 14 | Film and paper "a fair portrait of how Eastman Kodak made its money" | SUPPORTED (hedged) | K96 Item 1: "Kodak supplies films, photographic papers, processing services, photographic chemicals, cameras…". K96 MD&A: "Strong color film and paper volumes, photofinishing increases … led the gains" |
| 15 | "The awkward twist is that the threat came from inside." | **IMPRECISE** | See note C |
| 16 | 1975 researcher built "what the company calls the world's first digital camera" | SUPPORTED | MIL 1975 + 2010 |
| 17 | "toaster-sized … black-and-white images of 10,000 pixels" | SUPPORTED | MIL 1975: "captured black-and-white images at a resolution of 10,000 pixels (.01 megapixels)" |
| 18 | "Kodak did not miss the future. It invented it…" | SUPPORTED (interpretive, rests on 16) | MIL 1975 |
| 19 | FY1996 report: camera, film and photofinishing at minimum; photofinishing needs chemicals and paper | SUPPORTED | K96 Item 1, Consumer Imaging (p.2), verbatim |
| 20 | "Kodak supplied all of it." | SUPPORTED | K96 Item 1 (p.2): films, papers, processing services, chemicals, cameras |
| 21 | "The camera opened the door; the pictures paid the rent." | SUPPORTED as metaphor | See §5: no filing gives camera vs consumable margins |
| 22 | Each roll "another chance to sell film, paper, chemicals or processing" | SUPPORTED (mechanism) | K96 Item 1. K03 Item 1: "adding to the consumption of film and paper"; "supplies photographic papers and chemicals to other entities that provide photofinishing services" |
| 23 | FY1996 "about $16.0 billion of sales" | SUPPORTED | K96 income statement (p.13) and K98: $15,968M |
| 24 | FY1996 "roughly $7.6 billion of gross profit" | SUPPORTED | K98: 7,641 (1996 column). K96: 15,968 − 8,326 = 7,642; K96 Note 18 quarterly GP sums to 7,642 |
| 25 | "close to 48 cents of every sales dollar" | SUPPORTED | 47.85–47.86% |
| 26 | Digital camera: no roll, no film, no chemicals, no lab for the next picture | SUPPORTED (mechanism) | K03/K05 Item 1 substitution passages |
| 27 | FY2003 report: substitution hit mainly film and paper sales and processing services | **IMPRECISE** (minor) | See note D |
| 28 | Expected U.S. consumer film industry volumes to fall 10%–12% in 2004 | SUPPORTED | K03 Item 1 Photography (p.4): "consumer film industry volumes will decline in the U.S. by 10% to 12% and worldwide by 7% to 9%" |
| 29 | Lesson callout ("ask what the new product deletes…") | N/A (opinion) | — |
| 30 | Professional digital camera system in 1991 | SUPPORTED | MIL 1991: "The KODAK Professional Digital Camera System (DCS) was introduced" |
| 31 | EasyShare consumer cameras launched 2001 | SUPPORTED | MIL 2001: "The KODAK EASYSHARE System, a new line of digital cameras and docking systems … was launched." |
| 32 | 100 millionth digital still camera in 2011 ("by its own count") | SUPPORTED | MIL 2011 |
| 33 | "Kodak did go digital, and seriously." | SUPPORTED (interpretive) | K05 Item 1: "$3 billion investment and acquisition plan"; digital > traditional |
| 34 | 2003 strategy "paired" disciplined traditional management for cash with faster digital growth | **IMPRECISE** (minor) | See note E |
| 35 | By FY2005 digital revenues had overtaken traditional ones | SUPPORTED | K05 Item 1 (as #9) |
| 36 | "about $14.3 billion of sales" | SUPPORTED | K05 MD&A: "Net worldwide sales were $14,268 million for 2005" |
| 37 | Margin "slipped to 25.6% from 29.1% a year earlier" | SUPPORTED | K05 MD&A; 3,651/14,268 = 25.59%, 3,935/13,517 = 29.11% |
| 38 | Report named consumer digital cameras first in price/mix declines | SUPPORTED | K05 MD&A: "(1) price/mix, driven primarily by consumer digital cameras, one-time-use cameras, traditional consumer and digital health products" |
| 39 | FY2003 report: rapid price declines soon after launch were common | SUPPORTED | K03 Item 1 (p.6): "Rapid price declines shortly after product introduction in this environment are common" |
| 40 | "This is a value trap of strategy: a business too profitable to abandon." | N/A (opinion) | Framing; not a stock verdict (§3) |
| 41 | "The further Kodak's sales shifted toward hardware sold once under steady price pressure, the thinner its margin became." | **IMPRECISE** | See note F |
| 42 | Chapter 11 filed January 2012 | SUPPORTED | 8-K 2012-01-19 Item 1.03: "On January 19, 2012 … filed voluntary petitions for relief … under chapter 11" |
| 43 | February 2012: would stop making digital cameras, pocket video cameras, digital picture frames | SUPPORTED | 8-K 2012-02-13 Item 2.05: "a plan to exit its dedicated capture devices business consisting of digital cameras, pocket video cameras and digital picture frames" |
| 44 | December 2012: agreed to sell digital imaging patents "for about $525 million" | **IMPRECISE** (minor) | See note G |
| 45 | Emerged September 2013 | SUPPORTED | 8-K 2013-09-03 Item 3.03: "On September 3, 2013 … the Debtors emerged from their chapter 11 cases" |
| 46 | "after spinning off its personalized imaging and document imaging businesses" | SUPPORTED | Ex. 99.1 (2013-09-03): "Kodak completed the final steps in its Chapter 11 restructuring, including the spin-off of its Personalized Imaging and Document Imaging businesses to Kodak Pension Plan" |
| 47 | FY2025: "far smaller manufacturer," about $1.1 billion revenue | SUPPORTED | K25 Item 1: "Kodak is a global manufacturer…". Statement of operations: "Total net revenues 1,069" |
| 48 | Three business segments | SUPPORTED | K25 Item 1: "three reportable segments: Print, Advanced Materials and Chemicals, and Brand" |
| 49 | Print bullet: plates, presses, inks, workflow software | SUPPORTED | K25 Item 1, Print: "digital offset plate offerings … production press systems, consumables (primarily ink) … software"; Software: "PRINERGY workflow production software" |
| 50 | AM&C bullet: still, industrial, motion picture film, specialty chemicals, patent licensing | SUPPORTED | K25 Item 1, AM&C: Industrial Film and Chemicals ("professional and consumer still photographic film, as well as industrial film"; "Specialty Chemicals"); "Motion Picture"; "IP Licensing" (narration risk in §5) |
| 51 | Brand bullet: licensing the Kodak name to other companies | SUPPORTED | K25 Item 1: "The Brand segment includes licensing of the Kodak brand to third parties." |
| 52 | New plate for every print run; filing calls it a recurring revenue stream | SUPPORTED | K25 Item 1, Prepress: "a new plate must be used for each printing run. As a result, there is a recurring revenue stream from the sale of these plates." |
| 53 | Plate-making business about half of FY2025 revenue | SUPPORTED | K25 Item 1: "Net sales for the Prepress Solutions business accounted for 52%, 54% and 56% of Kodak's total net revenue for the years ended December 31, 2025, 2024 and 2023" |
| 54 | "rebuilt itself around a consumable again, sold to printers instead of households" | SUPPORTED (interpretive) | #52–53. K25: "The goal of Prepress Solutions is to pursue a contract-based, stable and recurring cash flow-generative business model." Print serves commercial print, direct mail, publishing… |
| 55 | 1996 vs 2005 margins describe different companies (businesses sold and bought) | **IMPRECISE** | See note H |
| 56 | Hindsight caveat | N/A (opinion) | — |

### Notes on the non-SUPPORTED items, with verbatim source and fix

**A (#3), subtitle "profits came from".** No filing states where Kodak's profits came from by product. In FY1996, Consumer Imaging (amateur film, paper, processing, chemicals and cameras) was $7,659M of $15,968M sales (48%) and $1,141M of $1,845M segment earnings from operations (62%) (K96 Segment Information). The rest came from Commercial Imaging. Per the K96 MD&A, that included Office Imaging, Entertainment Imaging, Business Imaging Systems, and Digital and Applied Imaging. What the filings do say is K03 MD&A (Photography GP 2003 vs 2002): "a decrease in sales of higher margin traditional products, the impact of which was only partially offset by increased sales of lower margin digital products." K05 says the same for 2004.
**Fix:** "…but whose richest margins came from film, paper, chemicals and processing, the very things that camera made unnecessary." Cite K03/K05 for it.

**B (#8), "Sales Up".** Reported 2005 sales did rise 6%. But K05 MD&A says: "This increase in net sales was primarily attributable to the acquisitions of KPG, Creo and NexPress, which contributed $1,562 million or approximately 11.6 percentage points to sales… These increases were partially offset by declines in volumes and declines in price/mix, which decreased 2005 sales by approximately 1.9 and 4.6 percentage points." Without the acquisitions, sales fell. Digital sales did rise: "digital product sales … were $7,757 million for 2005 as compared with $5,532 million … or 40%."
**Fix:** title "Digital Up, Margin Down".

**C (#15), "the threat came from inside".** The technology first came from inside the company (MIL 1975). The competitive threat came from outside. K03 Item 1: "The Company faces competition from other electronics manufacturers in this market, particularly on price and technological advances." K05 Item 1 names phone cameras.
**Fix:** "The awkward twist is that the threat was first built inside the company."

**D (#27), dropped geographic qualifier.** K03: "While this substitution to date has had an impact primarily on the Company's film and paper sales, and processing services in the U.S., Japan and Western Europe, …" The same paragraph cites growth potential in emerging markets.
**Fix (optional, minor):** "…had hit mainly its film and paper sales and processing services in the U.S., Japan and Western Europe, and it expected…"

**E (#34), "paired".** K03 Item 1: "There are four key elements of the strategy: – Disciplined management of traditional imaging businesses to maximize cash generation over an extended period of time – Accelerated growth in the Company's existing portfolio of digital imaging products and services – Tightly focused and highly disciplined acquisition strategy… – New, longer-term organic growth opportunities in areas such as electronic displays and inkjet." "Paired" implies there were only two.
**Fix:** "Its 2003 strategy combined disciplined management of the traditional business for cash with faster digital growth, among other goals." Or: "Its 2003 strategy began with disciplined management of the traditional business for cash and faster digital growth."

**F (#41), "hardware sold once".** The filings do support "the more sales shifted to digital, the thinner the margin". K03 and K05 both say "decrease in sales of higher margin traditional products … only partially offset by increased sales of lower margin digital products." But the 2005 digital revenue that overtook traditional was not mostly one-time hardware. K05 MD&A: digital product sales were $7,757M in total. Of that, Graphic Communications digital was $2,461M, "primarily attributable to the KPG, Creo and NexPress acquisitions" (KPG sells plates, a consumable). Health digital was $1,748M, including DryView media. D&FIS digital was $3,412M, driven by "the consumer digital capture SPG, the kiosks/media portion of the consumer output SPG, and the home printing SPG".
**Fix:** "The further Kodak's sales shifted from film toward lower-margin digital products, led by cameras under steady price pressure, the thinner its margin became."

**G (#44), $525M.** 8-K 2012-12-21 Item 1.01: Kodak "entered into certain agreements relating to the monetization of certain of its intellectual property assets, including the sale of its digital imaging patents… Kodak will receive approximately $525 million pursuant to the transactions contemplated by the above agreements." Part of that sum is paid by 12 named licensees for license rights. MIL 2012 does word it as "approximately $525 million for sale of its digital imaging patents", so the article's shorthand matches Kodak's own history page but not the 8-K.
**Fix:** "…and in December agreed to sell and license its digital imaging patents in deals worth about $525 million."

**H (#55), comparability caveat incomplete.** The reason given is true. Office Imaging was sold (K96 Danka agreement; K99 MD&A "divestiture of Office Imaging"), and KPG and Creo were acquired (K05). But it leaves out a bigger, quantifiable difference. The FY2005 cost of goods sold carries restructuring charges, while FY1996 reported restructuring ($358M) on its own line outside cost of goods sold (K96 income statement). K05 Note (Restructuring): "$391 million and $37 million of charges related to accelerated depreciation and inventory write-downs, respectively, which were reported in cost of goods sold." That $428M is about 3.0 points of 2005 margin; adding it back gives 28.6%. K05 MD&A also records $139M of extra depreciation from shortened useful lives "as a result of the faster-than-expected decline in the Company's traditional film and paper business".
**Fix:** "The 1996 and 2005 margins describe different companies: Kodak sold and bought whole businesses in between, and its 2005 cost of goods sold included about $430 million of restructuring charges. So the comparison shows direction, not precision." With "Two honest limits." and the hindsight sentence, the paragraph comes to about 71 words. That is under the 75-word block limit, but tight. The body would go from 657 to about 669 words, still inside 560–680.

### Derived arithmetic, recomputed

| Figure | Recomputed | Article | Result |
|---|---|---|---|
| FY1996 margin | 7,641 / 15,968 = 47.85% (K98); 7,642 / 15,968 = 47.86% (K96) | "close to 48 cents", "~48%" | OK |
| FY1996 sales | $15,968M | "$16.0 billion" | OK |
| FY1996 gross profit | $7,641M | "$7.6 billion" | OK |
| FY2005 margin | 3,651 / 14,268 = 25.59% | 25.6% | OK |
| FY2004 margin | 3,935 / 13,517 = 29.11% | 29.1% | OK |
| FY2005 sales | $14,268M | "$14.3 billion" | OK |
| FY2025 revenue | 715 + 316 + 23 = 1,054; + 15 All Other = 1,069 | "$1.1 billion" | OK |
| Prepress share FY2025 | 52%, about $556M | "about half" | OK |

## 3. Rule-break findings

None. The project checker prints `CLEAN` (657 body words, 15 blocks). Checked by hand:

- No person named: the 1975 inventor is "a Kodak researcher".
- No quotation.
- No time-relative word: "a year earlier" is anchored to fiscal 2005.
- No wrongdoing vocabulary.
- No share price, market cap or advice. The old common stock's cancellation is correctly left out.
- "Value trap of strategy" uses the app's category term, but it is explicitly redefined as a strategy trap. It is not a verdict on the stock. That is acceptable, though it borders on valuation language.

## 4. Claims the ledger missed

- **Title "…It Couldn't Sell":** not ledgered (see §5).
- **#15 "the threat came from inside":** not ledgered; ledger rows 2–4 skip this sentence.
- **#8 highlight title "Sales Up, Margin Down":** ledger H7 covers only the description, not the "Sales Up" title. The rise was acquisition-driven.
- **"The trouble was the kind of revenue it was":** an interpretive bridge, not ledgered. It is fine if note F is fixed.
- **Ledger gap on support.** The ledger never cites the filings' most direct support for the thesis: K03 MD&A Photography GP and K05 MD&A D&FIS GP 2004 vs 2003, "decrease in sales of higher margin traditional products … lower margin digital products". It should back the subtitle, the card subtitle and the warning callout.
- **Ledger omission on comparability.** It does not mention the 2005 cost-of-goods-sold restructuring charges ($391M + $37M) or the $139M useful-life depreciation (note H).

## 5. Could be misread (not counted as verdicts)

- **Title "Couldn't Sell".** The article's own facts say Kodak did sell digital: a 100 millionth camera shipped (MIL 2011), and digital revenue exceeded traditional in 2005. A literal listener hears a contradiction. Optional: "Kodak: Inventing the Future That Ate Its Film" or "…the Future It Couldn't Profit From". The second needs care: the filings say "lower margin", not unprofitable.
- **"The pictures paid the rent."** This implies cameras earned little and consumables earned the profit. No filing gives camera vs consumable margins. The closest support is the K03/K05 "higher margin traditional products" line, and traditional products also include film and one-time-use cameras. It is acceptable as a metaphor; do not harden it into a figure.
- **The ~48% FY1996 margin is company-wide.** It sits right after the consumer film-and-prints mechanism, but Commercial Imaging was 52% of FY1996 segment sales. Per the K96 MD&A that included Office Imaging, Entertainment Imaging, Business Imaging Systems, and Digital and Applied Imaging. Optional: "Across all its businesses, Kodak reported about $16.0 billion…"
- **Narration ambiguity in bullet #50.** "Advanced Materials and Chemicals: still, industrial and motion picture film…" When read aloud, "still" can sound like "continues to". Use "still-photo, industrial and motion picture film" or "photographic, industrial and motion picture film".
- **"Stop making" digital cameras.** The 8-K says Kodak would "seek licensees for cameras". K25 says the brand is licensed for "digital, instant print and 35mm film cameras". Kodak-branded cameras kept appearing under licence. The article's wording (Kodak stopped making them) is accurate; the bullet "Brand: licensing the Kodak name" covers the rest.
