# Verifier B: kodak-inventing-the-future-it-couldnt-sell.json

## 1. Verdict counts

**47 claims: 42 SUPPORTED · 5 IMPRECISE · 0 UNSUPPORTED · 0 WRONG.** In addition there are 7 misread risks (section 3b). I found no rule breaks, and `check_article.py` prints CLEAN (657 body words, 15 blocks).

I wrote these verdicts before opening the writer's ledger or the files in `SCR/src/`. Every source below I downloaded myself into `SCR/verify/src-B/kodak/`.

### Sources (downloaded by B)
| Key | Document | EDGAR accession / URL | Local file |
|---|---|---|---|
| K96 | Form 10-K, fiscal 1996 (filed 1997-03-14) | 0000031235-97-000001 | `fy1996_10k.txt` |
| K03 | Form 10-K, fiscal 2003 (filed 2004-03-15) | 0000031235-04-000049 (`ek10-k2003.txt`) | `fy2003_10k.txt` |
| K04 | Form 10-K, fiscal 2004 (filed 2005-04-06) | 0001206774-05-000535 | `fy2004_10k.txt` |
| K05 | Form 10-K, fiscal 2005 (filed 2006-03-02) | 0001206774-06-000357 (`ek111562.htm`) | `fy2005_10k.txt` |
| K13 | Form 10-K, fiscal 2013 (filed 2014-03-19) | 0001193125-14-106388 | `fy2013_10k.txt` |
| K25 | Form 10-K, fiscal 2025 (filed 2026-03-12) | 0001193125-26-104214 (`kodk-20251231.htm`) | `fy2025_10k.txt` |
| 8K-Jan12 | 8-K, Item 1.03 (event 2012-01-19) | 0001193125-12-016443 | `8k_20120119_ch11.txt` |
| 8K-Feb12 | 8-K, Item 2.05 (filed 2012-02-13) | 0000031235-12-000030 | `8k_20120213_exit.txt` |
| 8K-Dec12 | 8-K, Item 1.01 (filed 2012-12-21) and 8-K Item 8.01 (2012-12-19) | 0001193125-12-513381 / 0001193125-12-507443 | `8k_20121221.txt`, `8k_20121219_patents.txt` |
| MS | Kodak corporate Milestones page (kodak.com › company › milestones) | fetched 2026-11-01 | `kodak_milestones.txt` |

(K11 and K12, the fiscal 2011 and 2012 10-Ks, were also downloaded. No verdict depends on them.)

### Arithmetic, recomputed
- **FY1996:** sales $15,968M − cost of goods sold $8,326M = **$7,642M gross profit**. The four quarterly "Gross profit" lines in Note 18 (2,016 + 1,997 + 2,017 + 1,612) also sum to **$7,642M**. Margin: 7,642 / 15,968 = **47.86%**, so "close to 48 cents" and "~48%" are honest.
- **FY2005:** 3,651 / 14,268 = **25.59%**. FY2004: 3,935 / 13,517 = **29.11%**. The drop is **3.5 points**. All three figures match the filing's own 25.6% / 29.1%.
- **Digital vs traditional:**

  | Year | Digital | Traditional | Digital ahead? |
  |---|---|---|---|
  | 2004 | $5,532M | $7,985M | no |
  | 2005 | $7,757M | $6,511M | yes, first year |

  The two 2005 figures sum to $14,268M, which ties to net sales.
- **FY2025:** consolidated revenue **$1,069M**, so "about $1.1 billion" is honest. Prepress Solutions was 52% of revenue, about **$556M**, so "about half" is honest.
- **Fiscal years checked:** FY1996 → 10-K filed 1997; FY2003 → filed 2004 (its film forecast is for 2004); FY2005 → filed 2006; FY2025 → filed 2026. All correct.

## 2. Claim-by-claim verdicts

| # | Article wording (short) | Verdict | Source + location |
|---|---|---|---|
| 1 | Subtitle: "own researcher built a digital camera in 1975" | SUPPORTED | MS 1975: "Kodak invented the world's first digital camera"; MS 2010: "Retired Kodak researcher … for invention of the digital camera in 1975." |
| 2 | Subtitle: "profits came from film, paper, chemicals and processing, the very things that camera made unnecessary" | **IMPRECISE** | See fix A below. |
| 3 | cardSubtitle: "It built the digital camera, but film paid the bills." | SUPPORTED (interpretive) | MS 1975 + K96 segment data. Consumer Imaging (amateur films, papers, chemicals, photofinishing) earned $1,141M of $1,845M operating earnings in 1996, K96 p.8. See misread risk 3. |
| 4 | KH1: "researcher built a digital camera prototype in 1975, about the size of a toaster" | SUPPORTED | MS 1975: "The prototype was the size of a toaster." |
| 5 | KH2: "Film, paper, chemicals and processing could earn again each time someone took a photo" | SUPPORTED (inference from the stated mechanism) | K96 Item 1, Consumer Imaging, p.2 (see #12) |
| 6 | KH3: "Digital revenue overtook traditional revenue in 2005" | SUPPORTED | K05 Item 1, p.4–5: "For the year ended December 31, 2005, Kodak achieved a level of digital revenues that exceeded traditional revenues." K04 p.5 still lists this as a 2005 goal, and 2004 digital ($5,532M) was below traditional ($7,985M), so 2005 is the first year. |
| 7 | KH3: "gross margin fell to 25.6%" | SUPPORTED | K05 MD&A "Gross Profit", p.38 |
| 8 | Cover paragraph: 35mm roll in a metal canister; film and paper are "a fair portrait of how Eastman Kodak made its money" | SUPPORTED (nothing false about the company) | K96 Item 1: Kodak "supplies films, photographic papers…". I cannot see the image itself. See misread risk 7. |
| 9 | "In 1975 a Kodak researcher built what the company calls the world's first digital camera" | SUPPORTED | MS 1975 + MS 2010 (as #1). The claim is correctly attributed to the company. |
| 10 | "toaster-sized prototype that captured black-and-white images of 10,000 pixels" | SUPPORTED | MS 1975: "captured black-and-white images at a resolution of 10,000 pixels (.01 megapixels)" |
| 11 | "Kodak did not miss the future. It invented it…" | SUPPORTED (rhetorical, follows the attributed #9) | See misread risk 3 |
| 12 | FY1996 report: amateur photography needed at minimum a camera, film and photofinishing; photofinishing needed chemicals and paper | SUPPORTED | K96 Item 1, Consumer Imaging, p.2: "traditional amateur photography requires, at a minimum, a camera, film and photofinishing. Photofinishing requires equipment and supplies, including chemicals and paper for prints." Dropping "traditional" and "equipment" is harmless. |
| 13 | "Kodak supplied all of it." | SUPPORTED | K96 p.2: "For amateur photography, Kodak supplies films, photographic papers, processing services, photographic chemicals, cameras and projectors." |
| 14 | "The camera opened the door; the pictures paid the rent. Each roll bought, developed and printed was another chance to sell…" | SUPPORTED (interpretive) | K96 p.2 mechanism; K05 p.50: "higher margin traditional products … lower margin digital products". No filing splits camera profit from consumable profit. See misread risk 5. |
| 15 | FY1996 "about $16.0 billion of sales" | SUPPORTED | K96 Consolidated Statement of Earnings, p.13: Sales $15,968M. (Total revenues were $16,244M; "sales" is the right line.) |
| 16 | FY1996 "roughly $7.6 billion of gross profit" | SUPPORTED | K96 p.13: Sales − COGS ($8,326M) = $7,642M. Note 18, p.36 reports quarterly gross profit summing to $7,642M. |
| 17 | "close to 48 cents of every sales dollar" | SUPPORTED | 47.86% (recomputed) |
| 18 | "the next picture needs no film, no chemicals and no lab" | SUPPORTED (true of taking the picture) | Product mechanism; K03 p.5 confirms substitution hit film, paper and processing. Printing a digital picture can still use paper and a lab (see #2). |
| 19 | FY2003 report: "Digital substitution had hit mainly its film and paper sales and processing services" | **IMPRECISE** | See fix B below. |
| 20 | Expected "U.S. consumer film industry volumes to fall 10% to 12% in 2004" | SUPPORTED | K03 Item 1, Photography segment, p.4: "For 2004, the Company estimates that consumer film industry volumes will decline in the U.S. by 10% to 12% and worldwide by 7% to 9% primarily due to digital substitution." |
| 21 | "introduced a professional digital camera system in 1991" | SUPPORTED | MS 1991: "The KODAK Professional Digital Camera System (DCS) was introduced" |
| 22 | "launched EasyShare consumer cameras in 2001" | SUPPORTED | MS 2001: "The KODAK EASYSHARE System, a new line of digital cameras and docking systems … was launched." |
| 23 | "by its own count, shipped its 100 millionth digital still camera in 2011" | SUPPORTED | MS 2011: "Kodak shipped its 100 millionth digital still camera" |
| 24 | "Its 2003 strategy paired disciplined management of the traditional business for cash with faster digital growth" | SUPPORTED | K03 Item 1, p.3: "Disciplined management of traditional imaging businesses to maximize cash generation…" and "Accelerated growth in the Company's existing portfolio of digital imaging products and services". The strategy had four elements; the other two are acquisitions and new organic growth. Optional: "Its 2003 strategy began with…". |
| 25 | "By fiscal 2005 digital revenues had overtaken traditional ones" | SUPPORTED | As #6. See misread risk 2. |
| 26 | FY2005 "about $14.3 billion of sales" | SUPPORTED | K05 Consolidated Statement of Operations: Net sales $14,268M |
| 27 | "gross margin slipped to 25.6% from 29.1% a year earlier" | SUPPORTED | K05 p.38: "The gross profit margin was 25.6% in the current year as compared with 29.1% in the prior year." See misread risk 1. |
| 28 | "the annual report named consumer digital cameras first in explaining the price and mix declines" | SUPPORTED | K05 p.38: "(1) price/mix, driven primarily by consumer digital cameras, one-time-use cameras, traditional consumer and digital health products and services…" |
| 29 | FY2003 report "warned that rapid price declines soon after a model's launch were common" | SUPPORTED | K03 Item 1, p.6: "Rapid price declines shortly after product introduction in this environment are common, as producers are continually introducing new models…" The context is consumer digital products. |
| 30 | Callout: "The further Kodak's sales shifted toward hardware sold once under steady price pressure, the thinner its margin became." | **IMPRECISE** | See fix C below. |
| 31 | "Kodak filed for Chapter 11 in January" 2012 | SUPPORTED | 8K-Jan12 Item 1.03: "On January 19, 2012, Eastman Kodak Company … and its U.S. subsidiaries … filed voluntary petitions for relief … under chapter 11". The foreign subsidiaries did not file. |
| 32 | "said in February it would stop making digital cameras, pocket video cameras and digital picture frames" | **IMPRECISE** | See fix D below. |
| 33 | "in December agreed to sell its digital imaging patents for about $525 million" | SUPPORTED | 8K-Dec12 Item 1.01: "On December 18, 2012, … entered into certain agreements relating to the monetization of certain of its intellectual property assets, including the sale of its digital imaging patents … Kodak will receive approximately $525 million". MS 2012: "agreements that would bring it approximately $525 million for sale of its digital imaging patents". |
| 34 | "It emerged in September 2013" | SUPPORTED | K13 p.3: "On September 3, 2013 (the 'Effective Date'), the Plan became effective and the Debtors emerged from chapter 11 protection." MS 2013. |
| 35 | "…after spinning off its personalized imaging and document imaging businesses" | **IMPRECISE** | See fix E below. |
| 36 | FY2025 report "describes a far smaller manufacturer" | SUPPORTED | K25 Item 1, p.3: "Kodak is a global manufacturer focused on commercial print and advanced materials and chemicals." $1.07B against $16.0B in 1996. |
| 37 | "about $1.1 billion of revenue" (FY2025) | SUPPORTED | K25 MD&A, p.20: consolidated total $1,069M |
| 38 | "three business segments" | SUPPORTED | K25 p.3: "three reportable segments: Print, Advanced Materials and Chemicals, and Brand." The small "All Other" line is Eastman Business Park. |
| 39 | Print: "digital offset printing plates, presses, inks and workflow software" | SUPPORTED | K25 p.3: "digital offset plate offerings and computer-to-plate ('CTP') imaging solutions, production press systems, consumables (primarily ink) … software" |
| 40 | Advanced Materials and Chemicals: "still, industrial and motion picture film, specialty chemicals and patent licensing" | SUPPORTED | K25 p.4–5: "professional and consumer still photographic film, as well as industrial film"; "Specialty Chemicals"; "Motion Picture"; "IP Licensing". See misread risk 4 (narration). |
| 41 | Brand: "licensing the Kodak name to other companies" | SUPPORTED | K25 p.5: "The Brand segment includes licensing of the Kodak brand to third parties." |
| 42 | "A new plate is needed for every print run, which the filing calls a recurring revenue stream" | SUPPORTED | K25 p.3: "a new plate must be used for each printing run. As a result, there is a recurring revenue stream from the sale of these plates." |
| 43 | "the plate-making business supplied about half of fiscal 2025 revenue" | SUPPORTED | K25 p.4: "Net sales for the Prepress Solutions business accounted for 52% … of Kodak's total net revenue for the years ended December 31, 2025". Prepress also includes CTP plate-imaging equipment, but "the core of the business is the manufacturing of aluminum digital printing plates". Optional: "its prepress business, built around those plates". |
| 44 | "Kodak sold and bought whole businesses in between [1996 and 2005]" | SUPPORTED | K04: Remote Sensing Systems "sold to ITT Industries in August 2004". K05 p.37–38: "acquisitions of KPG, Creo and NexPress, which contributed $1,562 million … to sales" in 2005. K96: Office Imaging sold to Danka on December 31, 1996. MS 2001 and 2003: Bell & Howell imaging, Ofoto, PracticeWorks and others. |
| 45 | Stat: "1975 · Digital Camera Prototype" | SUPPORTED | MS 1975 |
| 46 | Stat: "~48% · Gross Margin, Fiscal 1996" | SUPPORTED | 47.86% (recomputed from K96) |
| 47 | Stat: "25.6% · Gross Margin, Fiscal 2005" | SUPPORTED | K05 p.38 |

### Fixes for the non-SUPPORTED claims (verbatim source + exact replacement)

**A. Claim 2, subtitle: IMPRECISE.**

The problem is the phrase "the very things that camera made unnecessary". Digital capture removed the need for film and processing. It did not make paper unnecessary. Kodak kept selling paper and print media for digital pictures, and the 2003 report describes this.

- K03 p.4: "Other digitization options have been created to stimulate more pictures in the use and printing of these images, adding to the consumption of film and paper."
- K05 p.38: digital growth was "driven by … the kiosks/media portion of the consumer output SPG, and the home printing SPG".

"profits came from film, paper, chemicals and processing" is right in direction only. K96 p.8 shows Consumer Imaging (films, papers, chemicals, photofinishing) with $1,141M of $1,845M operating earnings. No filing attributes Kodak's profit to those four items alone.

**Replacement:** "A company whose own researcher built a digital camera in 1975, but whose profits leaned on film, paper, chemicals and processing, the sales digital substitution hit hardest."

Alternative: keep the sentence and change "made unnecessary" to "made optional".

**B. Claim 19, FY2003 report: IMPRECISE.** The article drops the geographic qualifier.

- K03 p.5: "While this substitution to date has had an impact primarily on the Company's film and paper sales, and processing services in the U.S., Japan and Western Europe, the Company's strategy is to offset this…"
- The same passage notes "potential for significant growth in the sale of sensitized products outside the U.S."

**Replacement:** "Digital substitution had hit mainly its film and paper sales and processing services in the U.S., Japan and Western Europe, and it expected U.S. consumer film industry volumes to fall 10% to 12% in 2004."

**C. Claim 30, callout: IMPRECISE.** "Hardware sold once" overstates what Kodak's digital revenue was.

- K05 p.38: "The Company's digital product sales … were $7,757 million for 2005 … primarily driven by the digital portion of KPG and Creo, the consumer digital capture SPG, the kiosks/media portion of the consumer output SPG, and the home printing SPG."

That revenue includes graphic-arts plates and workflow from acquired businesses, plus kiosk media and home printing (consumables), as well as cameras. The supportable mechanism is in K05 p.50: "the impact of digital replacement, resulting in a decrease in sales of higher margin traditional products, the impact of which was only partially offset by increased sales of lower margin digital products."

**Replacement:** "The further Kodak's sales shifted toward lower-margin digital products, led by cameras under steady price pressure, the thinner its margin became."

**D. Claim 32, February 2012: IMPRECISE.** The filing describes exiting a business, not stopping production. Kodak-branded digital cameras continued under licence.

- 8K-Feb12 Item 2.05: "On February 9, 2012, Eastman Kodak Company (the Company) announced a plan to exit its dedicated capture devices business consisting of digital cameras, pocket video cameras and digital picture frames."
- K25 p.5: "Kodak currently licenses its brand for use with a range of products including digital, instant print and 35mm film cameras".

**Replacement:** "said in February it would exit its business of digital cameras, pocket video cameras and digital picture frames".

**E. Claim 35, 2013 emergence: IMPRECISE.** The 10-K describes a sale to the U.K. pension plan, completed on the emergence day itself, not a spin-off beforehand.

- K13 p.71: "On the Effective Date, Kodak completed the sale of substantially all of its assets constituting the Personalized Imaging and Document Imaging businesses to KPP Holdco Limited."
- K13 p.24: "On the Effective Date, Kodak received net cash consideration of $325 million for the sale of certain assets and liabilities of its Personalized Imaging and Document Imaging businesses as part of the KPP Global Settlement…"

Kodak's Milestones page (MS 2013) does say "spun off". That is why this is IMPRECISE rather than WRONG.

**Replacement:** "It emerged in September 2013, the same day it completed the sale of its personalized imaging and document imaging businesses to its U.K. pension plan."

Shorter alternative: "…completing on the same day the sale of its personalized imaging and document imaging businesses."

## 3. Rule-break findings

**3a. Hard rule breaks: none.**
- No investment advice, share price or market cap.
- No person named. Only "a Kodak researcher".
- No direct quotation. "What the company calls the world's first digital camera" is a paraphrase.
- No time-relative words. My scan hit "still" twice: "digital still camera" and "still, industrial…", both meaning still photography. It also hit "already" and "soon", both anchored to fiscal 2003 and to a model's launch.
- No wording of wrongdoing.
- `check_article.py` prints CLEAN.

Borderline: "This is a value trap of strategy" (and the category `valueTraps`). "Value trap" is an investing term for a stock that looks cheap. The article qualifies it ("of strategy") and it is the app's own category name, so the risk is low. If the editors want zero investing-verdict vocabulary in the body, use "This is the trap of a business too profitable to abandon."

**3b. Misread risks (a careful reader could take away a claim the sources do not make)**

1. **The ~48% vs 25.6% comparison carries restructuring charges on one side only.**
   - K05 p.58: of 2005 restructuring costs, "$391 million and $37 million of charges related to accelerated depreciation and inventory write-downs, respectively, … were reported in cost of goods sold".
   - K05 p.38: 2005 cost of goods sold also includes "$139 million of additional depreciation expense related to the change in estimate of the useful lives of production machinery and equipment as a result of the faster-than-expected decline in the Company's traditional film and paper business."
   - Restructuring sat outside cost of goods sold in 1996 (a separate $358M line, K96 p.13).

   Without the $428M, the 2005 margin is about 28.6%. The "Two honest limits" paragraph mentions only bought and sold businesses. **Suggested addition:** "…and its 2005 cost of sales carried about $428 million of restructuring charges, roughly 3 points of margin, so the comparison shows direction, not precision." Sources: K05 p.58 / p.38.
2. **The digital crossover and "Sales Up" partly reflect acquisitions, not cameras.**
   - K05 p.37–38: the 6% rise in 2005 sales was "primarily attributable to the acquisitions of KPG, Creo and NexPress, which contributed $1,562 million or approximately 11.6 percentage points to sales". Volumes and price/mix declined.
   - Digital growth was "primarily driven by the digital portion of KPG and Creo" before consumer digital capture.

   A listener will assume the crossover came from camera sales. The article never says that outright, but "The trouble was the kind of revenue it was" points that way. Fix C covers the callout.
3. **The "world's first" claim slips into the article's own voice.** It is attributed once ("what the company calls"), but "It invented it", the KH1 title "Invented at Home" and the cardSubtitle "It built **the** digital camera" adopt it without attribution. The Milestones page is Kodak's own claim. Optional: cardSubtitle "It built a digital camera in 1975, but film paid the bills."
4. **Narration of the Advanced Materials bullet.** "still, industrial and motion picture film" read aloud sounds like the adverb "still". Replace with "still-photo, industrial and motion picture film" (K25: "still photographic film").
5. **"The camera opened the door; the pictures paid the rent."** This should not be heard as "cameras were sold cheaply or at a loss". No filing I read says that, and one-time-use cameras were themselves among the margin-relevant traditional products (K05 p.38). The line is acceptable as a metaphor for the repeat-purchase mechanism in K96.
6. **"three business segments"** is accurate. The 10-K also reports a small "All Other" line, $15M of revenue from Eastman Business Park. No change needed.
7. **Cover paragraph.** "The object above is a photographic print of film … the paper it is printed on" is literally true only if the cover art is a photo print. I could not see the image. Nothing it says about Kodak is false: Kodak made 35mm film and photographic paper (K96). No change needed unless the art is clearly not a print.

## 4. Claims the ledger missed or cited wrongly

I opened the ledger only after sections 1–3 were written. The ledger is thorough: every article sentence has an entry, and the excerpts I spot-checked against my own copies are verbatim and correctly located. These included K96 p.2 and p.13, K03 p.3–6 and the 32.2%/35.9% line, all the K05 strings, the 8-Ks and K25. The gaps are about wording against sources, not about missing sources.

1. **H3 (subtitle, "the very things that camera made unnecessary") is cited to claims 10–11, but the ledger's own evidence for claim 7 contradicts it.** Claim 7 quotes K03 p.4: "Other digitization options have been created to stimulate more pictures in the use and printing of these images, adding to the consumption of film and paper." Paper was not made unnecessary. See fix A.
2. **Claim 11 cites the right passage, but the article drops part of it.** The ledger's excerpt includes "in the U.S., Japan and Western Europe". The article omits it. See fix B.
3. **Claim 20 (callout) leans on segment-only evidence.** Its supporting excerpts are D&FIS digital sales "primarily driven by the consumer digital capture SPG". The ledger leaves out the company-level sentence on the same K05 page: 2005 digital sales were "primarily driven by the digital portion of KPG and Creo, the consumer digital capture SPG, the kiosks/media portion of the consumer output SPG, and the home printing SPG." Nothing in the ledger supports "hardware sold once". See fix C.
4. **Claim 22's own exhibit argues against "stop making".** The ledger's Ex. 99.1 excerpt says Kodak would "phase out its dedicated capture devices business … Kodak will instead expand its current brand licensing program, and seek licensees for cameras." Kodak-branded cameras continued under licence (K25 Brand). The filing's verb is "exit" / "phase out". See fix D.
5. **Claim 25: the ledger's 8-K Exhibit 99.1 (2013-09-03) does use "spin-off"**: "Kodak completed the final steps in its Chapter 11 restructuring, including the spin-off of its Personalized Imaging and Document Imaging businesses to Kodak Pension Plan". This is a document I did not download. It makes "spin-off" Kodak's own wording, so the remaining problem with claim 35 is "after". Both the press release and K13 ("On the Effective Date, Kodak completed the sale…") put the transaction on the emergence day itself, as one of the final restructuring steps. K13 calls it a sale. **Refined fix for #35:** "It emerged in September 2013, completing on the same day the spin-off of its personalized imaging and document imaging businesses to its U.K. pension plan." The verdict stays IMPRECISE.
6. **Claim 33 (caveat) misses the larger comparability issue.** 2005 cost of goods sold carried $391M of accelerated depreciation and $37M of inventory write-downs from restructuring, plus $139M of extra depreciation from shortened asset lives (K05 p.38, p.58). Restructuring sat outside 1996 cost of goods sold. The ledger's only margin adjustment note is K98's 49.9% ex-Office Imaging figure for 1996, which moves the comparison the other way. See misread risk 1.
7. **The acquisition effect on the 2005 crossover is not flagged.** Claim 15b quotes K05's "2005 acquisitions of KPG and Creo" but does not note that, per K05, acquisitions contributed $1,562M (about 11.6 points) of 2005 sales and drove most of the digital increase. "Kodak did go digital, and seriously" and the crossover are true, but the digital lead in 2005 was partly bought, not organic. See misread risk 2.
8. **One heading has no ledger entry.** The KH3 title "Sales Up, Margin Down" claims sales rose. Net sales did rise 6% ($13,517M → $14,268M, K05 p.37), but only because acquisitions added about 11.6 points while volumes and price/mix fell. The claim is true as stated, but the ledger covers only the description line (H7), not the title.
9. **Minor.** Claim 8 uses K98's restated 1996 cost of goods sold ($8,327M, gross profit $7,641M). K96 as originally filed gives $8,326M and $7,642M. The difference is immaterial, and both round to $7.6 billion and about 48%.

None of the ledger's sources is non-primary, and I found no excerpt that misquotes its source.
