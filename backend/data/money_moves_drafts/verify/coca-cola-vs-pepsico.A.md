# Verifier A: "Coca-Cola vs. PepsiCo" (`coca-cola-vs-pepsico.json`)

**Verdicts: 45 SUPPORTED / 10 IMPRECISE / 0 UNSUPPORTED / 1 WRONG.** The cover paragraph is not counted because it makes no claim about either company.

## 0. Provenance: are the writer's copies genuine?

I fetched everything below myself into `verify/src-A-coca-cola/`. My conversion scripts are in `verify/toolsA-coca-cola/`. I did not use the writer's `.txt` files or `htm2txt.py`.

| Source | Independent check | Result |
|---|---|---|
| KO 10-K FY2025 | `data.sec.gov/submissions/CIK0000021344.json`: newest 10-K is acc. **0001628280-26-010047**, filed 2026-02-20, period 2025-12-31, primary doc `ko-20251231.htm`. No later 10-K/A. The filing index (`…/0001628280-26-010047-index.htm`) lists `ko-20251231.htm` as the 10-K. A WebFetch of the EDGAR browse page shows the same accession. | My download is **byte-identical** to the writer's (`sha256 b69978f2…77dd`) |
| PEP 10-K FY2025 | `CIK0000077476.json`: newest 10-K is acc. **0000077476-26-000007**, filed 2026-02-03, period 2025-12-27, primary doc `pep-20251227.htm`. No later 10-K/A. The index confirms it. | **Byte-identical** (`sha256 326389cf…e4de`) |
| KO "New Coke" history page | Re-fetched (HTTP 200) | **Byte-identical** |
| KO "Coca-Cola History" (birth) page | Re-fetched (HTTP 200) | **Byte-identical** |
| PEP newsroom story (Lay's, July 3 2026) | Re-fetched (HTTP 200) | The HTML differs only in an Incapsula anti-bot token. The visible text is **identical**. |

I checked excerpts against my own text conversion, well over the six required. Every 10-K excerpt cited in the ledger was found word for word: claims H7, 8, 9, 10, 11, 12, 13, 15, 17, 18, 19, 27, 29–32, 34–36, 38, 39, 43, 46 and 47. All the KO-1985, KO-1886 and PEP-1965 excerpts were found too. **No excerpt was fabricated.** The problems below are about what the article says beyond the excerpts, or about excerpts that do not fully support the article's wording.

## 1. Every claim

S = SUPPORTED, I = IMPRECISE, U = UNSUPPORTED, W = WRONG. Page numbers are the printed 10-K pages.

| # | Article wording (short) | V | Source and location | Note, or verbatim source plus exact fix |
|---|---|---|---|---|
| 1 | Subtitle: "The same cola rivalry" | S | PEP Item 1 p. 6–7: "The Coca-Cola Company is our primary beverage competitor" | Not in the ledger |
| 2 | Subtitle: "one mostly sells concentrate to independent bottlers and tends its brands" | S | KO Item 7 p. 37 (59%); Item 1 p. 7; Item 1A p. 19 | |
| 3 | Subtitle: "the other makes, bottles and delivers much of what it sells" | S | PEP Item 1 p. 2–4 (own plants, DSD); Note 1 fn (a) (36%) | |
| 4 | Subtitle: "takes most of its revenue from foods" | S | PEP Note 1 p. 67: "PepsiCo \| 42 % \| 58 %" | |
| 5 | cardSubtitle "Near-identical colas"; tagLabel "SAME DRINK"; §1 "the two famous colas are near-twins"; lesson callout "almost the same product" | **I** | Only the shared recipe type is sourced. KO Item 1 p. 3: bottlers "combine concentrates with still or sparkling water and sweeteners". PEP sells Pepsi concentrate to bottlers (Item 1 p. 3). No source says the two colas are near-identical. PEP Item 1 p. 7 says the products "compete primarily on the basis of … taste". | A reader can take "near-identical" or "same drink" as a claim about recipe or taste that no source makes. **Fix:** cardSubtitle → "Two colas made the same way, opposite business models behind them." · tagLabel → "TWO COLAS, TWO MODELS" · §1 → "In the can, the two famous colas are made the same basic way, from a flavour concentrate, sweetener and carbonated water." · callout → "Two companies can sell the same kind of product and still be very different businesses." |
| 6 | KH1: Concentrate operations 59% of KO revenue, FY2025 | S | KO Item 7 p. 37 / Note 20 | |
| 7 | KH2: Convenient foods 58%, beverages 42% of PEP FY2025 net revenue | S | PEP Note 1 p. 67 | |
| 8 | KH3: "Both filings say finished drinks bring more revenue but thinner margins than concentrate." | **I** | KO p. 4: "**Generally**, finished product operations generate higher net operating revenues but lower gross profit margins than concentrate operations." PEP p. 67 fn (a): "**Generally**, our finished goods beverage operations produce higher net revenue, but lower operating margins…" | Both filings qualify the statement with "Generally", and the article drops it. **Fix:** "Both filings say finished drinks generally bring more revenue but thinner margins than concentrate." |
| 9 | Stat ~$47.9B KO revenue FY2025 | S | KO income statement p. 61: Net Operating Revenues $47,941M | |
| 10 | Stat ~$93.9B PEP net revenue FY2025 | S | PEP income statement p. 57: Net Revenue $93,925M | |
| 11 | Stat 58% PEP revenue from foods | S | PEP Note 1 p. 67 | |
| 12 | Cover: two bare cans, condensation, one lit cold blue, one warm amber | n/a | Picture description | Says nothing factual about either company and gives no colour to either brand. I cannot see the image, so I cannot confirm the paragraph matches it. |
| 13 | Colas "each made from a flavour concentrate, sweetener and carbonated water" | S | KO Item 1 p. 3 (definition of concentrates; bottlers combine concentrate with water and sweeteners); PEP Item 1 p. 3–4 (Pepsi concentrate sold to bottlers; sweeteners, flavorings, water are principal ingredients; CSD = carbonated soft drinks) | |
| 14 | "One mostly sells the flavour and the name, and leaves the heavy lifting to partners." | S | KO p. 37 (59% / 85%); p. 7 ("Most of our branded beverage products are prepared, packaged, distributed and sold by independent bottling partners."); p. 5 (bottler's agreements authorize use of trademarks) | |
| 15 | PEP "makes, bottles and delivers a large share … most of its revenue from chips, cereals and other foods" | S | PEP p. 2–4; Note 1 (58%); PFNA "cereals, chips, dips…" | |
| 16 | KO began in Atlanta in 1886 as a syrup, mixed with carbonated water at a pharmacy soda fountain | S | KO-1886 page: "born in Atlanta, Georgia, on May 8, 1886 … produced the syrup … Jacobs' Pharmacy … as a soda fountain drink. Carbonated water was teamed with the new syrup" | |
| 17 | Two lines of business: concentrate operations and finished product operations | S | KO Item 1 p. 3–4 | |
| 18 | "Most of its drinks are made and sold by independent bottlers." | S | KO p. 7 | |
| 19 | "Under bottler's agreements, each buys its concentrate from Coca-Cola, adds water and sweetener, packages the drink and sells it in an assigned territory." | **I** | KO p. 5: "the bottler's agreements **generally** authorize the bottlers to prepare, package, distribute and sell … in (but, subject to applicable local law, **generally only in**) an identified territory. The bottler is obligated to purchase its entire requirement of concentrates or syrups … from the Company **or Company-authorized suppliers**." p. 3: sweeteners "(depending on the product)". | The article drops two qualifiers ("generally" and "or Company-authorized suppliers"). **Fix:** "Under bottler's agreements, each buys its concentrate from Coca-Cola or a supplier it authorizes, adds water and sweetener, packages the drink and sells it, generally only in an assigned territory." |
| 20 | Five largest bottlers were 44% of worldwide volume in FY2025 | S | KO p. 5: "In 2025, these five bottling partners combined represented 44% of our total worldwide unit case volume." | |
| 21 | KO revenue about $47.9B, FY2025 | S | KO p. 61 | |
| 22 | "Concentrate made up 59% of Coca-Cola's revenue … and 85% of its volume." | **I** (minor) | KO p. 37: "percentage of total net operating revenues attributable to **concentrate operations** … 59 %" and "percentage of total worldwide **unit case volume** attributable to concentrate operations … 85 %". Concentrate operations also sell syrups, fountain syrups and "certain finished beverages" (p. 3). | The figures are right but the label is shortened. KH1 uses the correct label. **Fix:** "Concentrate operations made up 59% of Coca-Cola's revenue of about $47.9 billion in fiscal 2025, and 85% of its unit case volume." |
| 23 | "finished drinks bring in more revenue, but at lower gross margins" | **I** | KO p. 4 (quoted at #8): "Generally, …" | The article drops "Generally". **Fix:** "…finished drinks generally bring in more revenue, but at lower gross margins." |
| 24 | KO calls its core business building brands and leading its system of bottlers | S | KO Item 1A p. 19 | |
| 25 | KO keeps selling company-owned bottling back to independent partners ("refranchising") | S | KO p. 19 ("we continue to seek opportunities to refranchise"); p. 7 ("from time to time we acquire or take control of a bottling operation … selling all or a portion of our interest … to an independent bottler") | |
| 26 | In 2025 it refranchised bottling in parts of India | S | KO Note 2 p. 73: "In May 2025, the Company refranchised our bottling operations in certain territories in India" | |
| 27 | "…sold its finished product operations in Nigeria", listed as an example of selling company-owned bottling to independent partners | **I** | KO Note 2 p. 73–74: "In October 2025, we sold our finished product operations in Nigeria" and "the Company's finished product operations in Nigeria, **which were included in the EMEA operating segment**". Company-owned bottling sits in the **Bottling Investments** segment (p. 4). The filing never calls the Nigeria sale refranchising or names the buyer. | The sale itself is real. Placing it under "selling company-owned bottling back to independent partners" claims what the filing does not. **Fix:** drop it from this list (see #28 for the full sentence). If it stays, give it its own sentence: "It also sold its finished product operations in Nigeria." |
| 28 | "…and agreed to sell part of its African bottling to one of its largest bottlers" | **I** | KO Note 2 p. 74: "entered into a definitive agreement to sell **a portion of our interest** in our bottling operations in Africa to CCHBC … upon which we will deconsolidate these bottling operations. We have also agreed to a separate option arrangement for CCHBC to acquire the Company's remaining 25% ownership interest". CCHBC is one of the five largest bottlers (p. 5). | "Part of its African bottling" reads as some of the operations. The deal is for part of Coca-Cola's **stake** in all of those operations. **Fix for the whole sentence:** "In 2025 it refranchised bottling in parts of India and agreed to sell part of its stake in its African bottling operations to one of its largest bottlers." |
| 29 | Callout: "Coca-Cola's own history shows how much rests on the name." | **I** | KO-1985 page: "What these tests didn't show, of course, was the bond consumers felt with their Coca‑Cola"; "Coca‑Cola was obviously more than just a soft drink." | In 1985 the formula changed and the name did not. The episode shows attachment to the original product and brand, not to the name, so the sentence does not follow. **Fix:** "Coca-Cola's own history shows how strongly drinkers are attached to the brand." |
| 30 | "In April 1985 it announced the first formula change in 99 years" | S | KO-1985: "April 23, 1985 … announcing that it was changing the formula"; "marking the first formula change in 99 years" (1886 + 99 = 1985) | The wording is lifted verbatim (see #33) |
| 31 | "a taste preferred in tests of nearly 200,000 consumers" | S | KO-1985: "adopting a formula preferred in taste tests of nearly 200,000 consumers" | Near-verbatim (see #33). Not overstated. |
| 32 | "After a firestorm of consumer protest, the original formula returned 79 days later as Coca-Cola classic." | S | KO-1985: "the firestorm of consumer protest that ensued"; "the return of the original formula, now called Coca‑Cola classic"; "The return of original formula Coca‑Cola on July 11, 1985, put the cap on 79 days". Arithmetic: Apr 23 → Jul 11 = 7 + 31 + 30 + 11 = **79**. | "Firestorm of consumer protest" is a verbatim lift (see #33) |
| 33 | The 1985 callout as written | **I** (wording) | Same page | The callout is attributed in its opening words ("Coca-Cola's own history"). Its three sentences, though, are stitched from the page's own phrases with no quotation marks: "first formula change in 99 years" (verbatim), "preferred in [taste] tests of nearly 200,000 consumers" (near-verbatim) and "firestorm of consumer protest" (verbatim). That is close copying, not paraphrase, and "firestorm" is the company's own dramatic word. The facts themselves are not overstated. **Fix (also fixes #29), 58 words:** "Coca-Cola's own history shows how strongly drinkers are attached to the brand. In April 1985 it announced its first change to the formula in 99 years, a recipe that tasters had preferred in tests involving nearly 200,000 consumers. Drinkers protested in large numbers, and 79 days later the company brought back the original formula as Coca-Cola classic." |
| 34 | PepsiCo formed in 1965 when Frito-Lay merged with Pepsi-Cola | S | PEP-1965 story: "In 1965, Frito-Lay, Inc. merged with Pepsi-Cola to form PepsiCo" | Note: PEP 10-K p. 2 says "We were incorporated in Delaware in 1919 and reincorporated in North Carolina in 1986". That is the legal entity. PepsiCo's own story supports "formed in 1965" for the company that took the PepsiCo name, so no change is needed. |
| 35 | "so snacks were there from its first day" | S | PEP-1965: Frito-Lay sold chips and Fritos before the 1965 merger | |
| 36 | Foods 58% of net revenue of about $93.9B, beverages 42% | S | PEP p. 57, p. 67 | |
| 37 | Six reporting segments | S | PEP p. 2: "We are organized into six reportable segments" | |
| 38 | Bullet: two North American units, one for foods and one for beverages | S | PEP p. 2 (PFNA, PBNA) | |
| 39 | Bullet: a franchise unit selling concentrate to bottlers internationally | S | PEP p. 2–3 (IB Franchise) | It also holds SodaStream and a Lipton joint venture. Leaving those out is acceptable. |
| 40 | Bullet: EMEA unit runs foods and company-owned bottlers | S | PEP p. 2 | |
| 41 | Bullet: food-only units for Latin America and Asia Pacific | S | PEP p. 3 | |
| 42 | PepsiCo does much of its bottling itself | S | PEP p. 3 (PBNA and EMEA run their own bottling plants); p. 67 fn (a) | Company-owned-bottler revenue is 36% of total revenue, against 42% for all beverages, so about 86% of beverage revenue |
| 43 | Its North American drinks business runs its own bottling plants | S | PEP p. 3 | |
| 44 | DSD, run with bottlers and distributors, takes drinks and foods to stores, where its staff or bottlers stock the shelves | S | PEP p. 4: "We, our independent bottlers and our distributors operate DSD systems that deliver beverages and convenient foods directly to retail stores where the products are merchandised by our employees or our independent bottlers." Also "DSD products are placed on the shelf by our employees". | |
| 45 | In FY2025 company-owned bottlers produced 36% of revenue | S | PEP p. 67 fn (a): "Beverage revenue from company-owned bottlers … is 36 % of our consolidated net revenue in 2025" | |
| 46 | PepsiCo took in roughly twice Coca-Cola's revenue | S | 93,925 ÷ 47,941 = **1.96** | |
| 47 | About 306,000 PEP employees against KO's 65,900, more than four times | S | PEP p. 10: "approximately 306,000 … as of December 27, 2025"; KO p. 10: "approximately 65,900" as of Dec 31, 2025. 306,000 ÷ 65,900 = **4.64** | Optional: "against Coca-Cola's roughly 65,900" |
| 48 | Own bottling and delivery give PepsiCo "more control over how products reach the shelf" | S (inference) | PEP p. 4: "DSD enables us to merchandise with maximum visibility and appeal"; merchandised "by our employees". KO p. 19: bottlers "make their own business decisions that may not always align with our interests". | A fair reading of the two filings side by side |
| 49 | "…and one route into stores for drinks and foods" | **W** | PEP Item 7 (MD&A, Our Business), p. 34: "**In North America, we are carefully evaluating an integrated model for our food and beverage supply chains, go-to-market, and commercial capabilities**". Item 1 p. 4 speaks of "DSD systems" (plural), and PBNA and PFNA are separate segments. | PepsiCo's filing says a combined food-and-beverage route to market is only under evaluation, so "one route into stores" contradicts it. The ledger's excerpt for claim 41 does not support this phrase. **Fix:** "Running its own bottling and store delivery gives PepsiCo a direct hand in how its drinks and foods are stocked and displayed on store shelves." |
| 50 | Selling concentrate gives KO "a leaner business built on its brands" | S (inference) | Headcount (#47); 59% concentrate (#22); KO p. 19 "core business of building brands" | |
| 51 | KO is reliant on bottlers that are independent contractors, not its agents | S | KO p. 5: "Our bottlers are independent contractors and are not our agents."; KO p. 19: "We rely on our bottling partners for a significant portion of our business." | |
| 52 | "Coca-Cola's lean figures leave out its bottlers, whose plants and people sit on their own books" | **I** (minor) | KO p. 2: "Company" means "all entities included in our consolidated financial statements". KO p. 4: consolidated bottling operations are in Bottling Investments, so their people **are** inside the 65,900. | Only the independent bottlers are left out. The next sentence says KO owns some bottling, so as written the two sentences contradict each other. **Fix:** "Coca-Cola's lean figures leave out its independent bottlers, whose plants and people sit on their own books;" |
| 53 | Companies in which KO holds sizeable stakes, including several large bottlers, had about $102.8B combined revenue in 2025 | S | KO Note 6 p. 85: equity-method investees "include, but are not limited to … CCEP; Monster; AC Bebidas; Coca-Cola FEMSA; CCHBC; and Coca-Cola Bottlers Japan" (18–28% stakes); aggregate "Net operating revenues \| $ \| 102,800" for 2025; footnote: "results of the equity method investees during the Company's period of ownership" | Note: the figure covers all equity-method investees, each for the period KO owned it. "Including several large bottlers" correctly allows for Monster, which is not a bottler. |
| 54 | "Coca-Cola still owns some bottling" | S | KO p. 4 (Bottling Investments); Item 2 p. 28: Bottling Investments owns 56 bottling plants and leases 5 | See §2 on the word "still" |
| 55 | PepsiCo also sells concentrate to franchise bottlers | S | PEP p. 3: IB Franchise "sells beverage concentrates to authorized and independent bottlers"; PBNA "also sells concentrate" | |
| 56 | Subheading: "Owning less, on purpose" | S | KO p. 19 (refranchising strategy) | Not in the ledger |
| 57 | "The cost shows in the filings." | S (framing) | Headcount comparison (#47) | Not in the ledger |

These carry no factual claim: the lesson callout's second sentence ("The model lives in two lines of the annual report…") and "Neither model is simply better; each pays for a different kind of control."

### Derived arithmetic, recomputed
- 93,925 / 47,941 = 1.959. "Roughly twice" is OK.
- 306,000 / 65,900 = 4.643. "More than four times" is OK.
- April 23 to July 11, 1985 = 79 days. OK.
- 1886 + 99 = 1985. OK, and consistent with "first formula change in 99 years".
- 59% concentrate plus 41% finished = 100% (KO). 42% plus 58% = 100% (PEP). 36% company-owned bottlers is below 42% beverages, which is consistent.
- $47,941M ≈ $47.9B, $93,925M ≈ $93.9B and $102,800M ≈ $102.8B. The rounding is honest.

## 2. Rule-break findings

- **No hard rule breaks.** `check_article.py` prints CLEAN (677 body words, 15 blocks). No person is named, there is no quotation mark, no advice or verdict on value, no share price, market cap or market share, and no wrongdoing wording.
- **Close copying of the company page** in the 1985 callout (#33). The rules ban quotation marks, so the fix is to paraphrase; the rewrite is given above.
- **"still"**, in "Coca-Cola still owns some bottling" (#54), is mildly time-relative. It is not on the banned list. Suggest "Coca-Cola also owns some bottling".
- **Misreadable claims:** "near-identical / same drink" (#5) and "one route into stores" (#49, which the filing contradicts).
- Style only, not a fact issue: the British spellings "aluminium" and "flavour" differ from both filings' US usage ("aluminum", "flavorings").

## 3. Claims the ledger missed, or covers only on paper

- **Missed:** "The same cola rivalry" (subtitle, supportable by PEP p. 7), tagLabel "SAME DRINK" (#5), subheading "Owning less, on purpose" (#56), "The cost shows in the filings" (#57), and "made opposite choices about what to own" (subtitle framing, supported by the two models).
- **Cited, but the excerpt does not support the article's wording:**
  - Ledger 18 (Nigeria): the filing places that operation in the EMEA segment and never calls it bottling or refranchising (#27).
  - Ledger 19 (Africa): the excerpt says "a portion of our interest", not part of the operations (#28).
  - Ledger 41 ("one route into stores"): nothing in the cited excerpt supports it, and PEP Item 7, p. 34 contradicts it (#49).
  - Ledger 20 ("rests on the name"): the page is about the bond with the brand, not the name (#29).
