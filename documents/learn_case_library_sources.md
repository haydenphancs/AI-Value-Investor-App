# Learn case library: source checklist

Verifier checklist for `backend/data/learn_case_library.json` (plan §1.6, `~/.claude/plans/improve-or-add-wiser-snoopy-sun.md`). Claude drafted every fact below from the linked primary source on 2026-10-22/23. **No entry is usable until a named verifier checks it**: the loader (`backend/app/services/learn_weekly/case_library.py`) treats an entry as usable only when `verified_by` and `verified_at` are both set and `llm_eligible` is true.

## Verification record (2026-10-23)

The owner delegated this check to Claude. Every fact was checked twice against its primary source, by two separate agents: a verifier that quoted the passage, and a skeptic that re-fetched the source and tried to disprove the verdict.

- **178** facts confirmed as written.
- **6** corrected to the source's own wording (marked ✎ below); both agents agreed on the new text.
- **1** rewritten (`mech-interconnection-queues:f8`): the agents disagreed on "over 5 years" versus "5 years", so the fact now states the figure both read in the report PDF (61 months) and cites that PDF.

Every entry now carries `verified_by: "AI check: 2 independent agents vs primary sources (owner-delegated)"` and `verified_at: 2026-10-23`. The label is deliberately not a person's name: put yours on any entry you re-check by hand. Spot-check a few facts per entry, the ✎ ones first, before the first live Deep Dive.

## How to verify

1. Open each fact's source link and confirm the sentence says what the source says: the same numbers, the same period, no added claim. SEC 10-K links open the full filing; use the browser's find (Cmd+F) on a distinctive number from the fact.
2. Tick the box. If a fact is wrong or you cannot confirm it, delete that fact from the JSON (fact ids need not be contiguous) rather than editing it into something the source does not say.
3. When every remaining fact in an entry is ticked, set that entry's `verified_by` (your name) and `verified_at` (ISO date, e.g. `2026-10-24`) in the JSON. A theme becomes eligible for a Deep Dive once it has at least 2 verified primers (mechanics or company entries).

## Notes for the verifier

- **Dates.** Sources with only a month or year published (`2024-12`, `2026-05`, `2003`, `2004`, `2007`) carry that partial date; web pages with no publication date (eCFR, two DOE pages, the FERC explainer) have `date: null`.
- **Blocked to scripts.** The LBNL data-centre report (eScholarship), the LBNL Queued Up page and the FERC explainer refuse command-line fetches; they open normally in a browser. The NRC site also refused, so no fact cites nrc.gov directly (see Dropped).
- **Estimates.** LBNL figures are model estimates, and each fact says "estimated" where the report does.
- **Duplicates on purpose.** A few 10-K facts appear in both a mechanics entry and a company entry (for example NVIDIA's 22% and 14% direct customers) so that either primer stands alone on a fact sheet.
- **Private counterparties.** OpenAI, Holtec Palisades and Sanmina appear only as parties to a public filer's or a government agency's disclosed fact, never with their own figures (plan §1.5).
- **Microsoft's FY2026 10-K** was filed 2026-07-29 and **Oracle's FY2026 10-K** 2026-06-22; both are the latest annual filings as of drafting.

**Totals:** 21 entries, 185 facts.

| Theme | Mechanics | Company | Facts |
|---|---|---|---|
| `silicon-rush` | 7 | 6 | 116 |
| `powering-machine` | 6 | 2 | 69 |

## Theme `silicon-rush`

### `mech-hyperscaler-capex-cycle` — The hyperscaler capex cycle

Kind: mechanics · Companies: MSFT (CIK 0000789019), GOOGL (CIK 0001652044), AMZN (CIK 0001018724), META (CIK 0001326801), ORCL (CIK 0001341439) · Era: 2023-2026 · Category fit: blueprints, valueTraps

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-hyperscaler-capex-cycle`
- [x] `mech-hyperscaler-capex-cycle:f1` Microsoft reported additions to property and equipment of $115,948 million for the fiscal year ended June 30, 2026, compared with $64,551 million for the fiscal year ended June 30, 2025 and $44,477 million for the fiscal year ended June 30, 2024.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `mech-hyperscaler-capex-cycle:f2` Alphabet reported purchases of property and equipment of $91,447 million for the year ended December 31, 2025, compared with $52,535 million for 2024 and $32,251 million for 2023.  
  Source: [Alphabet Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-05
- [x] `mech-hyperscaler-capex-cycle:f3` Amazon reported purchases of property and equipment of $131,819 million for the year ended December 31, 2025, compared with $82,999 million for 2024 and $52,729 million for 2023.  
  Source: [Amazon.com, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-06
- [x] `mech-hyperscaler-capex-cycle:f4` Meta reported purchases of property and equipment of $69,691 million for the year ended December 31, 2025, compared with $37,256 million for 2024 and $27,045 million for 2023.  
  Source: [Meta Platforms, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-01-29
- [x] `mech-hyperscaler-capex-cycle:f5` Oracle reported capital expenditures of $55,663 million for the fiscal year ended May 31, 2026, compared with $21,215 million for the fiscal year ended May 31, 2025 and $6,866 million for the fiscal year ended May 31, 2024.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `mech-hyperscaler-capex-cycle:f6` Microsoft reported that cost of revenue in its Intelligent Cloud segment increased $17.7 billion, or 44%, in the fiscal year ended June 30, 2026, driven by investments in AI infrastructure to support growing customer demand.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `mech-hyperscaler-capex-cycle:f7` As of June 30, 2026, Microsoft had additional leases, primarily for datacenters, that had not yet commenced totaling $329.1 billion, with lease terms of 1 year to 20 years.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `mech-hyperscaler-capex-cycle:f8` As of May 31, 2026, Oracle had $260 billion of additional lease commitments, substantially all related to data center arrangements, with terms of fifteen to nineteen years, that were not reflected on its consolidated balance sheet.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- Banned claims for the writer: Any forecast of future capital spending by any company; Any claim that a named company is overinvesting or that its spending will not pay off; Any statement that the AI build-out is a bubble.

### `mech-server-useful-life-depreciation` — Server useful lives and depreciation accounting

Kind: mechanics · Companies: AMZN (CIK 0001018724), META (CIK 0001326801), GOOGL (CIK 0001652044), MSFT (CIK 0000789019), ORCL (CIK 0001341439), CRWV (CIK 0001769628) · Era: 2023-2026 · Category fit: blueprints, valueTraps

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-server-useful-life-depreciation`
- [x] `mech-server-useful-life-depreciation:f1` Amazon changed its estimate of the useful lives of a subset of its servers and networking equipment from six years to five years, effective January 1, 2025, citing the increased pace of technology development, particularly in the area of artificial intelligence and machine learning.  
  Source: [Amazon.com, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-06
- [x] `mech-server-useful-life-depreciation:f2` Amazon reported that this change in estimate increased depreciation and amortization expense by $1.4 billion and reduced net income by $1.0 billion for the year ended December 31, 2025.  
  Source: [Amazon.com, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-06
- [x] `mech-server-useful-life-depreciation:f3` Meta increased the estimated useful lives of most of its servers and network assets to 5.5 years, effective January 1, 2025.  
  Source: [Meta Platforms, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-01-29
- [x] `mech-server-useful-life-depreciation:f4` Meta reported that this change in estimate reduced depreciation expense by $2.92 billion and increased net income by $2.59 billion for the year ended December 31, 2025.  
  Source: [Meta Platforms, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-01-29
- [x] `mech-server-useful-life-depreciation:f5` Alphabet's 10-K for 2025 states that it depreciates servers and network equipment generally over a period of six years and recognizes any change in estimated useful lives on a prospective basis.  
  Source: [Alphabet Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-05
- [x] `mech-server-useful-life-depreciation:f6` Microsoft's 10-K for the fiscal year ended June 30, 2026 gives an estimated useful life of two to six years for servers and network equipment.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `mech-server-useful-life-depreciation:f7` Oracle's 10-K for the fiscal year ended May 31, 2026 states that its computer, network, machinery and equipment consist primarily of servers and networking equipment with an estimated useful life of six years.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `mech-server-useful-life-depreciation:f8` CoreWeave changed its estimate of the useful life of the computing equipment used in its data centers from five to six years, effective January 1, 2023.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- Banned claims for the writer: Any claim that a company chose a useful life to inflate or manage earnings; Any claim that a useful-life estimate is improper, aggressive or misleading; Any statement of the economic life of a specific GPU model.

### `mech-take-or-pay-compute-contracts` — Take-or-pay contracts and backlog for compute

Kind: mechanics · Companies: CRWV (CIK 0001769628), ORCL (CIK 0001341439), MSFT (CIK 0000789019) · Era: 2023-2026 · Category fit: blueprints, valueTraps

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-take-or-pay-compute-contracts`
- [x] `mech-take-or-pay-compute-contracts:f1` CoreWeave's 10-K for 2025 states that the initial period of its committed contracts generally ranges from one to six years and that these contracts are typically structured as take-or-pay agreements requiring payment regardless of the level of utilization.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-take-or-pay-compute-contracts:f2` CoreWeave's 10-K states that customers under committed contracts often make a prepayment that is recorded as deferred revenue and consumed based on the terms of the contract.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-take-or-pay-compute-contracts:f3` Committed contracts accounted for over 98% of CoreWeave's revenue for the year ended December 31, 2025, compared with 96% for 2024 and 88% for 2023.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-take-or-pay-compute-contracts:f4` CoreWeave reported $60.7 billion of remaining performance obligations as of December 31, 2025, compared with $15.1 billion as of December 31, 2024.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-take-or-pay-compute-contracts:f5` CoreWeave's 10-K states that it primarily finances its infrastructure development through asset-level debt supported by take-or-pay customer contracts, supplemented by corporate-level equity and debt financing.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-take-or-pay-compute-contracts:f6` Oracle's 10-K defines remaining performance obligations as contracted revenues that had not yet been recognized, including deferred revenues, invoices issued but uncollected, and amounts to be invoiced and recognized as revenues in future periods.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `mech-take-or-pay-compute-contracts:f7` Oracle reported remaining performance obligations of $638 billion as of May 31, 2026, compared with $138 billion as of May 31, 2025, and attributed the increase primarily to certain significant cloud contracts entered into during the period.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `mech-take-or-pay-compute-contracts:f8` Oracle's 10-K states that the economic returns on its large, long-term cloud infrastructure investments depend on customer demand and on the ability of its key customers to meet their contractual obligations.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `mech-take-or-pay-compute-contracts:f9` Microsoft reported revenue allocated to remaining performance obligations of $684 billion as of June 30, 2026, of which $678 billion related to the commercial portion of revenue, with a weighted average duration of approximately 2.3 years.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- Banned claims for the writer: Any claim that remaining performance obligations are guaranteed revenue; Any claim that a named customer will or will not pay, renew or expand; Any forecast of when backlog will convert to revenue beyond what the filing states.

### `mech-telecom-fiber-overbuild` — The 1996-2002 telecom fiber overbuild

Kind: mechanics · Companies: none (generic) · Era: 1995-2003 · Category fit: valueTraps, blueprints

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-telecom-fiber-overbuild`
- [x] `mech-telecom-fiber-overbuild:f1` The Federal Reserve Bank of Richmond reported that U.S. investment in communications equipment grew from approximately $62 billion per year in the first quarter of 1996 to over $135 billion per year in the fourth quarter of 2000, in constant 1996 dollars.  
  Source: [Boom and Bust in Telecommunications, Economic Quarterly Volume 89/4 (Fall 2003)](https://fraser.stlouisfed.org/files/docs/publications/frbrichreview/rev_frbrich200304.pdf) — Federal Reserve Bank of Richmond, 2003
- [x] `mech-telecom-fiber-overbuild:f2` The same article reported that communications equipment investment fell to under $93 billion in the fourth quarter of 2001, only 69 percent of the level one year earlier.  
  Source: [Boom and Bust in Telecommunications, Economic Quarterly Volume 89/4 (Fall 2003)](https://fraser.stlouisfed.org/files/docs/publications/frbrichreview/rev_frbrich200304.pdf) — Federal Reserve Bank of Richmond, 2003
- [x] `mech-telecom-fiber-overbuild:f3` The article stated that by early 2001 it had become apparent that massive overinvestment had taken place in the telecommunications sector, particularly in long-distance fiber-optic cable.  
  Source: [Boom and Bust in Telecommunications, Economic Quarterly Volume 89/4 (Fall 2003)](https://fraser.stlouisfed.org/files/docs/publications/frbrichreview/rev_frbrich200304.pdf) — Federal Reserve Bank of Richmond, 2003
- [x] `mech-telecom-fiber-overbuild:f4` The article reported that consumption of telecommunications services grew steadily from approximately $88 billion in 1995 to $151 billion in 2001, in constant 1996 dollars.  
  Source: [Boom and Bust in Telecommunications, Economic Quarterly Volume 89/4 (Fall 2003)](https://fraser.stlouisfed.org/files/docs/publications/frbrichreview/rev_frbrich200304.pdf) — Federal Reserve Bank of Richmond, 2003
- [x] `mech-telecom-fiber-overbuild:f5` The article reported that telecommunications industry employment peaked at approximately 1.59 million workers in March 2001 and declined 22 percent to about 1.30 million by July 2003.  
  Source: [Boom and Bust in Telecommunications, Economic Quarterly Volume 89/4 (Fall 2003)](https://fraser.stlouisfed.org/files/docs/publications/frbrichreview/rev_frbrich200304.pdf) — Federal Reserve Bank of Richmond, 2003
- [x] `mech-telecom-fiber-overbuild:f6` The Federal Reserve Bank of San Francisco reported that capital investment by publicly traded telecom service companies rose from $47 billion in 1995 to a peak of $121 billion in 2000 and fell to $49 billion by 2002.  
  Source: [The Boom and Bust in Information Technology Investment, FRBSF Economic Review 2004](https://www.frbsf.org/wp-content/uploads/er19-34bk.pdf) — Federal Reserve Bank of San Francisco, 2004
- [x] `mech-telecom-fiber-overbuild:f7` A Federal Reserve Board working paper stated that more than 90 percent of the optical fiber cables installed during the 1990s were left unused in the years that followed.  
  Source: [Learning By Investing: Embodied Technology and Business Cycles (Finance and Economics Discussion Series 2007-15)](https://www.federalreserve.gov/pubs/feds/2007/200715/200715pap.pdf) — Board of Governors of the Federal Reserve System, 2007
- [x] `mech-telecom-fiber-overbuild:f8` The same paper noted that this level of unused fiber was also partly due to a new signal transmitting technology that dramatically increased the transmitting capacity of each cable.  
  Source: [Learning By Investing: Embodied Technology and Business Cycles (Finance and Economics Discussion Series 2007-15)](https://www.federalreserve.gov/pubs/feds/2007/200715/200715pap.pdf) — Board of Governors of the Federal Reserve System, 2007
- [x] `mech-telecom-fiber-overbuild:f9` A 2003 academic paper from the University of Minnesota estimated that U.S. Internet traffic had approximately doubled every year since 1997, and described 1995 and 1996 as an anomalous two-year period when traffic grew at about the rate of a doubling every 100 days.  
  Source: [Internet traffic growth: Sources and implications](https://www-users.cse.umn.edu/~odlyzko/doc/itcom.internet.growth.pdf) — University of Minnesota, 2003
  ✎ Corrected 2026-10-23: "only" removed. The paper calls 1995-96 anomalous and allows that the fast rate may have run into early 1997.
- [x] `mech-telecom-fiber-overbuild:f10` A 2026 Federal Reserve Board staff note, using Bureau of Economic Analysis data, reported that the U.S. intellectual property and equipment investment share of GDP reached 11.5 percent in 2000 and that as of the first quarter of 2026 it was about 0.8 percentage points above its 2024 level and slightly below the 2000 peak.  
  Source: [Do Major Technology Advancements Lead to Overinvestment? (FEDS Notes)](https://www.federalreserve.gov/econres/notes/feds-notes/do-major-technology-advancements-lead-to-overinvestment-20260706.html) — Board of Governors of the Federal Reserve System, 2026-07-06
- Banned claims for the writer: Any claim that the AI build-out is a bubble or will end like the telecom bust; Any claim that a named company today is overbuilding; Any account of fraud or misconduct at any telecom company of that era; Any stock index level or price move from the period.

### `mech-customer-concentration` — Customer concentration in AI chips and cloud

Kind: mechanics · Companies: NVDA (CIK 0001045810), AVGO (CIK 0001730168), CRWV (CIK 0001769628), AMD (CIK 0000002488), ORCL (CIK 0001341439) · Era: 2023-2026 · Category fit: valueTraps, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-customer-concentration`
- [x] `mech-customer-concentration:f1` NVIDIA reported that, for the fiscal year ended January 25, 2026, sales to one direct customer represented 22% of total revenue and sales to another direct customer represented 14% of total revenue, primarily attributable to its Compute & Networking segment.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-customer-concentration:f2` NVIDIA's 10-K defines direct customers as those that buy products directly from NVIDIA and indirect customers as those that buy through its direct customers, a group that includes cloud service providers, AI model makers, enterprises and public sector entities.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-customer-concentration:f3` NVIDIA reported that three direct customers accounted for 25%, 18% and 13% of its accounts receivable balance as of January 25, 2026.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-customer-concentration:f4` Broadcom reported that one customer accounted for 32%, 28% and 21% of its net revenue in fiscal years 2025, 2024 and 2023, and that revenue from this customer was included in its semiconductor solutions segment.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `mech-customer-concentration:f5` Broadcom's 10-K states that it believes aggregate sales through all channels to its top five end customers accounted for approximately 40% of its net revenue for each of fiscal years 2025 and 2024.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `mech-customer-concentration:f6` CoreWeave reported that it recognized approximately 67% of its revenue for the year ended December 31, 2025 from its top customer, Microsoft.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-customer-concentration:f7` CoreWeave reported that its top two customers accounted for approximately 77% of its revenue for the year ended December 31, 2024 and its top three customers for approximately 73% of its revenue for 2023.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `mech-customer-concentration:f8` AMD reported that no customer accounted for at least 10% of its consolidated net revenue in fiscal years 2025 and 2024, and that one Client and Gaming segment customer accounted for 18% in fiscal year 2023.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `mech-customer-concentration:f9` Oracle's 10-K states that no single customer accounted for 10% or more of its total revenues in fiscal 2026, 2025 or 2024.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- Banned claims for the writer: The identity of any customer a filing does not name; Any claim that a named customer will reduce, end or shift its purchases; Any ranking of which company is riskier because of concentration.

### `mech-ai-chip-export-controls` — Export controls on AI chips

Kind: mechanics · Companies: NVDA (CIK 0001045810), AMD (CIK 0000002488) · Era: 2022-2026 · Category fit: battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-ai-chip-export-controls`
- [x] `mech-ai-chip-export-controls:f1` The Bureau of Industry and Security published an interim final rule in the Federal Register on October 13, 2022, with provisions taking effect on October 7, October 12 and October 21, 2022, that amended the Export Administration Regulations to control advanced computing integrated circuits, computer commodities that contain them, and certain semiconductor manufacturing items.  
  Source: [Implementation of Additional Export Controls: Certain Advanced Computing and Semiconductor Manufacturing Items; Supercomputer and Semiconductor End Use; Entity List Modification (87 FR 62186)](https://www.federalregister.gov/documents/2022/10/13/2022-21658/implementation-of-additional-export-controls-certain-advanced-computing-and-semiconductor) — Federal Register (Bureau of Industry and Security), 2022-10-13
  ✎ Corrected 2026-10-23: the rule took effect in three steps (October 7, 12 and 21), not all on October 7.
- [x] `mech-ai-chip-export-controls:f2` The Bureau of Industry and Security published a further interim final rule on October 25, 2023, effective November 17, 2023, that revised the advanced computing controls of the October 7, 2022 rule.  
  Source: [Implementation of Additional Export Controls: Certain Advanced Computing Items; Supercomputer and Semiconductor End Use; Updates and Corrections (88 FR 73458)](https://www.federalregister.gov/documents/2023/10/25/2023-23055/implementation-of-additional-export-controls-certain-advanced-computing-items-supercomputer-and) — Federal Register (Bureau of Industry and Security), 2023-10-25
- [x] `mech-ai-chip-export-controls:f3` NVIDIA's 10-K states that in August 2022 the U.S. government announced export restrictions and licensing requirements targeting China's semiconductor and supercomputing industries that affected its A100 and H100 integrated circuits.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-ai-chip-export-controls:f4` NVIDIA's 10-K states that in October 2023 the U.S. government announced licensing requirements for exports to China and Country Groups D:1, D:4 and D:5 of products exceeding certain performance thresholds, including the A100, H100, GB200 NVL72 and B200.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-ai-chip-export-controls:f5` NVIDIA's 10-K states that in April 2025 the U.S. government required a license for exports of its H20 integrated circuits to China, including Hong Kong and Macau, and to D:5 countries.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-ai-chip-export-controls:f6` NVIDIA incurred a $4.5 billion charge in the first quarter of fiscal year 2026 associated with H20 excess inventory and purchase obligations.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-ai-chip-export-controls:f7` AMD's 10-K states that an April 2025 U.S. license requirement for exports of certain semiconductor products to D5 countries, a group that includes China, affected its AMD Instinct MI308 products and that it incurred approximately $800 million in inventory and related charges in the second quarter of 2025.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
  ✎ Corrected 2026-10-23: AMD's 10-K counts China as a D5 country; the old wording read them as separate. The same paragraph also says about $360 million was reversed in Q4 (net about $440 million for the year).
- [x] `mech-ai-chip-export-controls:f8` NVIDIA reported that revenue from customers headquartered outside the United States accounted for 31%, 41% and 48% of its total revenue for fiscal years 2026, 2025 and 2024.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- Banned claims for the writer: Any forecast of future export rules or licence decisions; Any claim about revenue a company would have earned without the controls; Any statement about government revenue-sharing arrangements.

### `mech-fabless-foundry-model` — The fabless chip model

Kind: mechanics · Companies: NVDA (CIK 0001045810), AMD (CIK 0000002488), AVGO (CIK 0001730168) · Era: 2025-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-fabless-foundry-model`
- [x] `mech-fabless-foundry-model:f1` NVIDIA's 10-K describes a fabless and contract manufacturing strategy in which it partners with key suppliers for all phases of the manufacturing process, including wafer fabrication, assembly, testing and packaging.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-fabless-foundry-model:f2` NVIDIA's 10-K states that this strategy lets it avoid many of the significant costs and risks associated with owning and operating manufacturing operations.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-fabless-foundry-model:f3` NVIDIA's 10-K states that it uses foundries such as Taiwan Semiconductor Manufacturing Company and Samsung to produce its semiconductor wafers and purchases memory from SK Hynix, Micron Technology and Samsung.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-fabless-foundry-model:f4` NVIDIA's 10-K states that its supply chain is currently mainly concentrated in Asia and that it is expanding into the U.S. and Latin America.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `mech-fabless-foundry-model:f5` AMD's 10-K states that it uses Taiwan Semiconductor Manufacturing Company for the production of wafers for its HPC, FPGA and adaptive SoC products.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `mech-fabless-foundry-model:f6` AMD's 10-K states that wafers for its products are either sorted by the foundry or delivered to assembly, test, mark and packaging partners or subcontractors in the Asia-Pacific region.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `mech-fabless-foundry-model:f7` Broadcom's 10-K states that the majority of its front-end wafer manufacturing operations is outsourced to external foundries, including Taiwan Semiconductor Manufacturing Company.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `mech-fabless-foundry-model:f8` Broadcom's 10-K states that it uses its internal fabrication facilities for products made with its proprietary processes, such as its FBAR filters for wireless communications.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- Banned claims for the writer: Any claim about a foundry's own capacity, pricing or market share; Any claim that a company's supply is secure or at risk beyond what its filing states.

### `co-nvidia` — NVIDIA: business model and segments

Kind: company · Companies: NVDA (CIK 0001045810) · Era: 2023-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-nvidia`
- [x] `co-nvidia:f1` NVIDIA reports its business results in two segments, Compute & Networking and Graphics.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f2` NVIDIA's Compute & Networking segment includes its Data Center accelerated computing and networking platforms, AI solutions and software, and Automotive platforms, while its Graphics segment includes GeForce GPUs for gaming and PCs and Quadro/NVIDIA RTX GPUs for enterprise workstation graphics.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f3` NVIDIA's total revenue was $215,938 million for the fiscal year ended January 25, 2026, compared with $130,497 million for the fiscal year ended January 26, 2025.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f4` NVIDIA's Data Center revenue was $193,737 million for the fiscal year ended January 25, 2026, made up of $162,361 million of compute revenue and $31,376 million of networking revenue.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f5` NVIDIA's networking offerings include NVLink interconnects and switches and end-to-end platforms for InfiniBand and Ethernet.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f6` NVIDIA introduced its CUDA development platform in 2006, opening the parallel processing capabilities of its GPUs to a broad range of compute-intensive applications.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f7` NVIDIA's acquisition of Mellanox in 2020 expanded its offerings to include networking.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f8` For the fiscal year ended January 25, 2026, sales to one direct customer represented 22% of NVIDIA's total revenue and sales to another direct customer represented 14%.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- [x] `co-nvidia:f9` NVIDIA was incorporated in California in April 1993 and reincorporated in Delaware in April 1998.  
  Source: [NVIDIA Corporation Form 10-K for the fiscal year ended January 25, 2026](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-25
- Banned claims for the writer: Any market-share figure for NVIDIA; Any claim that NVIDIA's position is permanent or under threat; The identity of NVIDIA's unnamed direct customers.

### `co-coreweave` — CoreWeave: business model and contracts

Kind: company · Companies: CRWV (CIK 0001769628) · Era: 2023-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-coreweave`
- [x] `co-coreweave:f1` CoreWeave was founded in September 2017 and launched its CoreWeave Cloud Platform in 2020.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f2` CoreWeave's 10-K states that prior to 2022 it had limited revenue, most of which came from past crypto mining offerings that were discontinued.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f3` CoreWeave reported revenue of $5,131 million for the year ended December 31, 2025, compared with $1,915 million for 2024 and $229 million for 2023.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f4` CoreWeave reported net losses of $1.2 billion, $863 million and $594 million for the years ended December 31, 2025, 2024 and 2023.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f5` CoreWeave operates its business in one operating segment.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f6` CoreWeave sells access to its platform either through committed contracts, which are take-or-pay, or on demand on a pay-as-you-go basis.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f7` CoreWeave recognized approximately 67% of its revenue for the year ended December 31, 2025 from its top customer, Microsoft.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f8` As of December 31, 2025, CoreWeave operated 43 data centers with over 850 MW of active power, compared with 32 data centers with approximately 360 MW of active power as of December 31, 2024.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f9` CoreWeave reported total contracted power capacity of approximately 3.1 GW as of December 31, 2025.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- [x] `co-coreweave:f10` CoreWeave reported $60.7 billion of remaining performance obligations as of December 31, 2025.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- Banned claims for the writer: Any claim that CoreWeave will or will not become profitable; Any figure for a private counterparty's own finances; Any claim about the terms of a contract beyond what the filing states.

### `co-microsoft` — Microsoft: segments and cloud

Kind: company · Companies: MSFT (CIK 0000789019) · Era: 2024-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-microsoft`
- [x] `co-microsoft:f1` Microsoft reports its financial performance in three segments: Productivity and Business Processes, Intelligent Cloud, and More Personal Computing.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f2` Microsoft's Intelligent Cloud segment consists of its public, private and hybrid server products and cloud services, including Azure and other cloud services.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f3` Microsoft's total revenue was $331,839 million for the fiscal year ended June 30, 2026, compared with $281,724 million for the fiscal year ended June 30, 2025.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f4` Microsoft's Intelligent Cloud segment revenue was $137,791 million for the fiscal year ended June 30, 2026, compared with $106,265 million for the prior fiscal year.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f5` Microsoft reported that Azure and other cloud services revenue grew 41% in the fiscal year ended June 30, 2026.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f6` Microsoft Cloud revenue, which includes Microsoft 365 Commercial cloud, Azure and other cloud services, the commercial portion of LinkedIn, and Dynamics 365, was $214.4 billion for the fiscal year ended June 30, 2026.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f7` Microsoft reported additions to property and equipment of $115,948 million for the fiscal year ended June 30, 2026, compared with $64,551 million for the prior fiscal year.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f8` Microsoft holds an investment in OpenAI accounted for under the equity method that represents an approximate 25% interest on an as-converted basis.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- [x] `co-microsoft:f9` Microsoft's other income included $6.5 billion of net gains for the fiscal year ended June 30, 2026 and $4.8 billion of net losses for the fiscal year ended June 30, 2025 from investments in OpenAI.  
  Source: [Microsoft Corporation Form 10-K for the fiscal year ended June 30, 2026](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-07-29
- Banned claims for the writer: Any valuation of OpenAI or any figure for OpenAI's own finances; Any claim about the future of the Microsoft and OpenAI partnership; Any market-share figure for Azure.

### `co-oracle` — Oracle: cloud infrastructure and backlog

Kind: company · Companies: ORCL (CIK 0001341439) · Era: 2024-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-oracle`
- [x] `co-oracle:f1` Oracle has three businesses, cloud and software, hardware, and services, and each business is comprised of a single operating segment.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f2` Oracle's cloud and software business represented 87% of its total revenues in fiscal 2026 and 86% in fiscal 2025.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f3` Oracle's total revenues were $67,357 million for the fiscal year ended May 31, 2026, compared with $57,399 million for the fiscal year ended May 31, 2025.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f4` Oracle's cloud infrastructure revenues were $18,101 million for fiscal 2026, compared with $10,234 million for fiscal 2025 and $6,840 million for fiscal 2024.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f5` Oracle's cloud applications revenues were $15,888 million for fiscal 2026, compared with $14,272 million for fiscal 2025.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f6` Oracle's 10-K states that its Oracle Cloud Infrastructure offerings are designed to deliver its infrastructure technologies, including compute, storage, networking and cloud-native services, along with solutions such as AI infrastructure, generative AI and agentic AI.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
  ✎ Corrected 2026-10-23: the 10-K says the offerings "are designed to deliver", not that they "deliver".
- [x] `co-oracle:f7` Oracle reported remaining performance obligations of $638 billion as of May 31, 2026, compared with $138 billion as of May 31, 2025.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f8` Oracle reported capital expenditures of $55,663 million for fiscal 2026, compared with $21,215 million for fiscal 2025.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f9` No single customer accounted for 10% or more of Oracle's total revenues in fiscal 2026, 2025 or 2024.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- [x] `co-oracle:f10` As of May 31, 2026, Oracle had $260 billion of additional lease commitments, substantially all related to data center arrangements, with terms of fifteen to nineteen years, that were not reflected on its consolidated balance sheet.  
  Source: [Oracle Corporation Form 10-K for the fiscal year ended May 31, 2026](https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/orcl-20260531.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-06-22
- Banned claims for the writer: Any claim that Oracle can or cannot fund its commitments; The identity of the counterparties to Oracle's large cloud contracts unless the filing names them; Any forecast of Oracle's cloud revenue.

### `co-amd` — AMD: segments and data-centre products

Kind: company · Companies: AMD (CIK 0000002488) · Era: 2023-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-amd`
- [x] `co-amd:f1` AMD has three reportable segments, Data Center, Client and Gaming, and Embedded, plus an All Other category that is not a reportable segment.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f2` AMD's Data Center segment primarily includes AI accelerators, CPUs for servers, GPUs, APUs, DPUs, AI NICs, FPGAs and SoC products for data centers.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f3` AMD's net revenue was $34,639 million for the fiscal year ended December 27, 2025, compared with $25,785 million for the fiscal year ended December 28, 2024.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f4` AMD's Data Center segment net revenue was $16,635 million for fiscal 2025, compared with $12,579 million for fiscal 2024, and the segment's operating income was $3,603 million for fiscal 2025.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f5` AMD's 10-K states that in the Data Center segment it competes primarily against Intel and NVIDIA with its CPU, GPU, DPU and AI NIC server products.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f6` AMD's 10-K states that it advanced its AI GPU roadmap to an annual cadence for AMD Instinct solutions, beginning with the AMD Instinct MI350 Series GPUs in 2025.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f7` On March 31, 2025, AMD completed the acquisition of ZT Systems, a provider of AI and general-purpose compute infrastructure for hyperscale computing companies, for total purchase consideration of $4.4 billion.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f8` On October 27, 2025, AMD completed the sale of the ZT Manufacturing Business to Sanmina Corporation for $2.4 billion in cash, subject to purchase price adjustments, plus shares of Sanmina common stock.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- [x] `co-amd:f9` No customer accounted for at least 10% of AMD's consolidated net revenue in fiscal years 2025 and 2024.  
  Source: [Advanced Micro Devices, Inc. Form 10-K for the fiscal year ended December 27, 2025](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-04
- Banned claims for the writer: Any market-share figure for AMD; Any claim that AMD will gain or lose share against a named rival.

### `co-broadcom` — Broadcom: semiconductors and infrastructure software

Kind: company · Companies: AVGO (CIK 0001730168) · Era: 2023-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-broadcom`
- [x] `co-broadcom:f1` Broadcom has two reportable segments, semiconductor solutions and infrastructure software.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f2` Broadcom's semiconductor solutions segment includes all of its semiconductor-based product lines and IP licensing, and its infrastructure software segment includes its private cloud, mainframe software, cybersecurity and enterprise software portfolios and its FC SAN business.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f3` Broadcom's total net revenue was $63,887 million for the fiscal year ended November 2, 2025, of which semiconductor solutions contributed $36,858 million and infrastructure software $27,029 million.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f4` Broadcom's AI semiconductor solutions include custom accelerators or XPUs, Ethernet switching and routing silicon, Ethernet NICs, physical layer devices and optical components, as well as racks and systems based on its XPUs.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f5` Broadcom's 10-K states that customers of its solutions for AI data center infrastructure are hyperscalers and companies with AI frontier models, as well as original equipment manufacturers and system integrators that develop servers, switches and racks deployed in large-scale AI data centers.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f6` Broadcom's 10-K attributes the increase in semiconductor solutions revenue for fiscal 2025 to strong demand for its networking solutions, primarily custom AI accelerators and AI networking products.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f7` One customer accounted for 32% of Broadcom's net revenue in fiscal 2025, 28% in fiscal 2024 and 21% in fiscal 2023, and that revenue was included in the semiconductor solutions segment.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f8` Sales to distributors accounted for 48% of Broadcom's net revenue for each of fiscal years 2025 and 2024.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- [x] `co-broadcom:f9` Broadcom acquired VMware, Inc. on November 22, 2023 in a cash-and-stock transaction.  
  Source: [Broadcom Inc. Form 10-K for the fiscal year ended November 2, 2025](https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/avgo-20251102.htm) — U.S. Securities and Exchange Commission (EDGAR), 2025-12-18
- Banned claims for the writer: The identity of Broadcom's largest customer or of its unnamed XPU customers; Any market-share figure for Broadcom.

## Theme `powering-machine`

### `mech-data-centre-electricity-demand` — How much electricity data centres use

Kind: mechanics · Companies: none (generic) · Era: 2014-2023 · Category fit: blueprints

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-data-centre-electricity-demand`
- [x] `mech-data-centre-electricity-demand:f1` A December 2024 Lawrence Berkeley National Laboratory report estimated that U.S. data centers consumed 176 TWh of electricity in 2023, representing 4.4% of total U.S. electricity consumption.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-electricity-demand:f2` The Department of Energy reported that total data center electricity usage climbed from 58 TWh in 2014 to 176 TWh in 2023.  
  Source: [DOE Releases New Report Evaluating Increase in Electricity Demand from Data Centers](https://www.energy.gov/articles/doe-releases-new-report-evaluating-increase-electricity-demand-data-centers) — U.S. Department of Energy, 2024-12-20
- [x] `mech-data-centre-electricity-demand:f3` The Lawrence Berkeley National Laboratory report estimated that data center electricity use remained fairly stable at about 60 TWh between 2014 and 2016.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-electricity-demand:f4` The report states that in 2017 GPU-accelerated servers for artificial intelligence became a significant enough portion of the data center server stock that total data center electricity use began to increase again.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-electricity-demand:f5` The report estimated that data centers consumed about 76 TWh in 2018, representing 1.9% of total U.S. electricity consumption.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-electricity-demand:f6` The report estimated that internal data centers accounted for over 60% of server energy consumption in 2014 and nearly 10% in 2023, while hyperscale and colocation data centers accounted for almost 80% in 2023.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-electricity-demand:f7` The Department of Energy describes data centers as one of the most energy-intensive building types, consuming 10 to 50 times the energy per floor space of a typical commercial office building.  
  Source: [Data Centers and Servers (Building Technologies Office)](https://www.energy.gov/eere/buildings/data-centers-and-servers) — U.S. Department of Energy, no date
- [x] `mech-data-centre-electricity-demand:f8` The report states that the Energy Act of 2020 called for the Department of Energy to publish an update to the laboratory's 2016 United States Data Center Energy Usage Report.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- Banned claims for the writer: Any projection of future data-centre electricity use, including the report's 2028 scenarios; Any claim that a named company's demand caused a specific share of this growth.

### `mech-interconnection-queues` — Grid interconnection queues

Kind: mechanics · Companies: none (generic) · Era: 2022-2026 · Category fit: blueprints

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-interconnection-queues`
- [x] `mech-interconnection-queues:f1` The Federal Energy Regulatory Commission issued Order No. 2023 on July 28, 2023 to reform the procedures and agreements that transmission providers use to connect new generating facilities to the transmission system.  
  Source: [Explainer on the Interconnection Final Rule (Order No. 2023, Docket No. RM22-14-000)](https://www.ferc.gov/explainer-interconnection-final-rule) — Federal Energy Regulatory Commission, no date
- [x] `mech-interconnection-queues:f2` FERC stated that as of the end of 2022 there were over 10,000 active interconnection requests in queues throughout the United States, representing over 2,000 gigawatts of potential generation and storage capacity.  
  Source: [Explainer on the Interconnection Final Rule (Order No. 2023, Docket No. RM22-14-000)](https://www.ferc.gov/explainer-interconnection-final-rule) — Federal Energy Regulatory Commission, no date
- [x] `mech-interconnection-queues:f3` FERC stated that of the 2,179 interconnection studies completed in 2022, 68% were issued late.  
  Source: [Explainer on the Interconnection Final Rule (Order No. 2023, Docket No. RM22-14-000)](https://www.ferc.gov/explainer-interconnection-final-rule) — Federal Energy Regulatory Commission, no date
- [x] `mech-interconnection-queues:f4` Order No. 2023 requires transmission providers to replace the serial first-come, first-served study process with a first-ready, first-served cluster study process that studies proposed generating facilities in batches.  
  Source: [Explainer on the Interconnection Final Rule (Order No. 2023, Docket No. RM22-14-000)](https://www.ferc.gov/explainer-interconnection-final-rule) — Federal Energy Regulatory Commission, no date
- [x] `mech-interconnection-queues:f5` Order No. 2023 requires interconnection customers to demonstrate 90% site control when they submit an interconnection request and 100% site control when they execute the facilities study agreement.  
  Source: [Explainer on the Interconnection Final Rule (Order No. 2023, Docket No. RM22-14-000)](https://www.ferc.gov/explainer-interconnection-final-rule) — Federal Energy Regulatory Commission, no date
- [x] `mech-interconnection-queues:f6` Order No. 2023 introduced penalties for transmission providers that miss study deadlines and withdrawal penalties for interconnection customers whose withdrawal materially affects the cost or timing of other requests with an equal or lower queue position.  
  Source: [Explainer on the Interconnection Final Rule (Order No. 2023, Docket No. RM22-14-000)](https://www.ferc.gov/explainer-interconnection-final-rule) — Federal Energy Regulatory Commission, no date
- [x] `mech-interconnection-queues:f7` Lawrence Berkeley National Laboratory reported that as of the end of 2025 about 8,200 projects, representing 1,312 GW of generation and approximately 749 GW of storage, were actively seeking grid interconnection in the U.S.  
  Source: [Queued Up: 2026 Edition, Characteristics of Power Plants Seeking Transmission Interconnection As of the End of 2025](https://emp.lbl.gov/publications/queued-2026-edition-characteristics) — Lawrence Berkeley National Laboratory, 2026-05
- [x] `mech-interconnection-queues:f8` The laboratory reported that the median project built in 2025 took 61 months from interconnection request to commercial operation.  
  Source: [Queued Up: 2026 Edition, Characteristics of Power Plants Seeking Transmission Interconnection As of the End of 2025 (report slides, PDF)](https://eta-publications.lbl.gov/sites/default/files/2026-06/queued_up_2026_edition.pdf) — Lawrence Berkeley National Laboratory, 2026-06
  ✎ Corrected 2026-10-23: rewritten. The two checkers disagreed on "over 5 years" versus "5 years"; both read "61 months" in the report PDF, which is now the cited source.
- [x] `mech-interconnection-queues:f9` The laboratory reported that only 13% of the capacity that submitted interconnection requests from 2000 to 2020 had reached commercial operation by the end of 2025, while 75% had been withdrawn and 10% was still active.  
  Source: [Queued Up: 2026 Edition, Characteristics of Power Plants Seeking Transmission Interconnection As of the End of 2025](https://emp.lbl.gov/publications/queued-2026-edition-characteristics) — Lawrence Berkeley National Laboratory, 2026-05
- [x] `mech-interconnection-queues:f10` The laboratory reported that active natural gas capacity in interconnection queues increased 86% in 2025 to 253 GW.  
  Source: [Queued Up: 2026 Edition, Characteristics of Power Plants Seeking Transmission Interconnection As of the End of 2025](https://emp.lbl.gov/publications/queued-2026-edition-characteristics) — Lawrence Berkeley National Laboratory, 2026-05
- Banned claims for the writer: Any forecast of how much queued capacity will be built; Any claim about when a specific project will connect; Any judgment that Order No. 2023 has or has not worked.

### `mech-power-purchase-agreements` — Power purchase agreements with data-centre buyers

Kind: mechanics · Companies: CEG (CIK 0001868275), VST (CIK 0001692819) · Era: 2024-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-power-purchase-agreements`
- [x] `mech-power-purchase-agreements:f1` In September 2024, Constellation executed a 20-year power purchase agreement with Microsoft that supports the restart of Three Mile Island Unit 1, renamed the Crane Clean Energy Center.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-power-purchase-agreements:f2` Constellation's 10-K states that under that agreement Microsoft is to purchase the output generated from the renewed plant, including energy, capacity and emissions-free attributes.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-power-purchase-agreements:f3` In June 2025, Constellation signed a 20-year power purchase agreement with Meta Platforms for the output of the Clinton Clean Energy Center, with the agreement beginning in June 2027.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-power-purchase-agreements:f4` In September 2025, Vistra announced a 20-year power purchase agreement, with options to extend for up to an additional 20 years, to supply Amazon Web Services with 1,200 MW of carbon-free power from its Comanche Peak Nuclear Power Plant.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-power-purchase-agreements:f5` In January 2026, Vistra announced 20-year power purchase agreements with Meta Platforms to supply 2,609 MW of carbon-free power and capacity from its PJM nuclear plants, including 2,176 MW of operating energy and capacity and 433 MW of uprate energy and capacity to be constructed.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-power-purchase-agreements:f6` Vistra's 10-K states that through its hedging, risk management and power purchase agreement activities it is exposed to the credit risk that counterparties do not perform their obligations.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- Banned claims for the writer: Any price or dollar value of a power purchase agreement, which the filings do not disclose; Any forecast of delivery dates beyond the contract terms the filings state; Any claim that a buyer will extend or add agreements.

### `mech-nuclear-licensing-restarts` — Nuclear licences, renewals and restarts

Kind: mechanics · Companies: CEG (CIK 0001868275), VST (CIK 0001692819) · Era: 2019-2026 · Category fit: blueprints

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-nuclear-licensing-restarts`
- [x] `mech-nuclear-licensing-restarts:f1` Under 10 CFR 50.51, each license, except certain non-power production or utilization facility licenses issued after January 29, 2025, is issued for a fixed period of time specified in the license that in no case may exceed 40 years from the date of issuance.  
  Source: [10 CFR 50.51 Continuation of license (current text)](https://www.ecfr.gov/current/title-10/section-50.51) — Electronic Code of Federal Regulations, None
  ✎ Corrected 2026-10-23: §50.51(a) excepts certain non-power facility licenses issued after January 29, 2025 (§50.51(c)).
- [x] `mech-nuclear-licensing-restarts:f2` Under 10 CFR 54.31, a renewed license is issued for the remaining years of the current license plus an additional period of not more than 20 years, the term of a renewed license may not exceed 40 years, and a renewed license may be renewed again.  
  Source: [10 CFR 54.31 Issuance of a renewed license (current text)](https://www.ecfr.gov/current/title-10/section-54.31) — Electronic Code of Federal Regulations, no date
- [x] `mech-nuclear-licensing-restarts:f3` Constellation's 10-K states that all of its nuclear units were originally licensed by the NRC for 40 years and have since received 20-year operating license renewals.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-nuclear-licensing-restarts:f4` Constellation's 10-K states that Peach Bottom and Dresden have received subsequent license renewals from the NRC for a second 20-year term, extending their operating period to a total of 80 years.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-nuclear-licensing-restarts:f5` Vistra's 10-K states that its application for license renewal at the Perry Nuclear Plant was approved by the NRC in July 2025 and that the license now extends through 2046.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-nuclear-licensing-restarts:f6` Constellation's 10-K states that Three Mile Island Unit 1, renamed the Crane Clean Energy Center, was retired in 2019 for economic reasons and that its restart is subject to regulatory approvals, including the NRC's comprehensive safety and environmental review, and to obtaining a renewed operating license.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-nuclear-licensing-restarts:f7` Constellation's 10-K states that in November 2025 the DOE Office of Energy Dominance Financing issued a guarantee for up to $1.0 billion for an unsecured loan from the Federal Financing Bank to support the restart of the Crane Clean Energy Center.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `mech-nuclear-licensing-restarts:f8` The Department of Energy states that in September 2024 it announced the closing of a $1.52 billion loan to Holtec Palisades, LLC to help finance restoring the 800-MW Palisades nuclear generating station in Covert Township, Michigan, which ceased operations in May 2022.  
  Source: [Palisades (Office of Energy Dominance Financing project page)](https://www.energy.gov/edf/palisades) — U.S. Department of Energy, no date
- [x] `mech-nuclear-licensing-restarts:f9` Vistra's 10-K states that nuclear units are generally operated at full capacity and that refueling outages are scheduled during the spring or fall off-peak demand periods.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-nuclear-licensing-restarts:f10` Constellation reported that its nuclear generating facilities achieved capacity factors of 94.7%, 94.6% and 94.4% in 2025, 2024 and 2023, at ownership percentage.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- Banned claims for the writer: Any date by which a plant will restart or deliver power; Any claim that a plant is safe or unsafe; Any claim about the outcome of a pending NRC review.

### `mech-data-centre-cooling` — Cooling the AI data centre

Kind: mechanics · Companies: CRWV (CIK 0001769628) · Era: 2014-2025 · Category fit: blueprints

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-data-centre-cooling`
- [x] `mech-data-centre-cooling:f1` The Lawrence Berkeley National Laboratory 2024 data center report defines power usage effectiveness as the total electricity demand of a data center divided by the electricity demand of its IT equipment.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-cooling:f2` The report defines water usage effectiveness as the total water consumption of a data center divided by the electricity demand of its IT equipment.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-cooling:f3` The report states that a waterside economizer is well suited for liquid cooling methods such as rear door heat exchangers, direct-to-chip cooling and immersion.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-cooling:f4` The report states that liquid-cooled IT systems can often be operated at higher water or refrigerant temperatures than air-cooled IT systems.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-cooling:f5` The report states that water-cooled chiller systems, while highly energy-efficient, consume substantial amounts of water, like all evaporative cooling systems.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-cooling:f6` The report estimated that U.S. data centers directly consumed 21.2 billion liters of water in 2014 and 66 billion liters in 2023, with hyperscale and colocation data centers accounting for 84% of the 2023 total.  
  Source: [2024 United States Data Center Energy Usage Report (LBNL-2001637)](https://escholarship.org/uc/item/32d6m0d1) — Lawrence Berkeley National Laboratory, 2024-12
- [x] `mech-data-centre-cooling:f7` The Department of Energy stated in May 2023 that data center cooling can account for up to 40% of data center energy usage overall.  
  Source: [DOE Announces $40 Million for More Efficient Cooling for Data Centers](https://www.energy.gov/articles/doe-announces-40-million-more-efficient-cooling-data-centers) — U.S. Department of Energy, 2023-05-09
- [x] `mech-data-centre-cooling:f8` In May 2023, the Department of Energy announced $40 million in funding for 15 projects under ARPA-E's COOLERCHIPS program to develop high-performance, energy-efficient cooling solutions for data centers.  
  Source: [DOE Announces $40 Million for More Efficient Cooling for Data Centers](https://www.energy.gov/articles/doe-announces-40-million-more-efficient-cooling-data-centers) — U.S. Department of Energy, 2023-05-09
- [x] `mech-data-centre-cooling:f9` CoreWeave's 10-K states that its data centers are designed to support high-density AI infrastructure and incorporate advanced networking equipment, enhanced access to power and, where appropriate, liquid cooling technologies.  
  Source: [CoreWeave, Inc. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-03-02
- Banned claims for the writer: Any projection of future data-centre water or cooling demand; Any claim that a named company's cooling technology is superior.

### `mech-wholesale-power-markets` — How wholesale power and capacity are priced

Kind: mechanics · Companies: VST (CIK 0001692819) · Era: 2025-2026 · Category fit: blueprints

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `mech-wholesale-power-markets`
- [x] `mech-wholesale-power-markets:f1` Vistra's 10-K states that in centrally dispatched market structures, such as ERCOT, PJM, ISO-NE, NYISO, MISO and CAISO, all generators receive the same price for energy based on the bid price of the last megawatt-hour needed to balance supply and demand.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-wholesale-power-markets:f2` Vistra's 10-K states that independent system operators and regional transmission organizations dispatch generation facilities and administer short-term energy and ancillary service markets, and that some also manage long-term planning reserves through capacity markets.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-wholesale-power-markets:f3` Vistra's 10-K states that PJM administers a forward capacity auction, the Reliability Pricing Model, which establishes a long-term market for capacity.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-wholesale-power-markets:f4` Vistra's 10-K states that ERCOT relies primarily on energy-market price signals, rather than mandatory capacity requirements or centralized capacity markets, to incentivize investment in and availability of generation resources.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-wholesale-power-markets:f5` Vistra's 10-K states that ERCOT manages the flow of electricity from approximately 83,707 MW of 2025 peak demand to approximately 27 million Texas customers, representing approximately 90% of the state's electric load.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-wholesale-power-markets:f6` Vistra's 10-K states that PJM manages the flow of electricity from approximately 160,709 MW of 2025 peak demand to approximately 67 million customers.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `mech-wholesale-power-markets:f7` Vistra's 10-K states that in 2025 the Texas legislature passed Senate Bill 6, which requires certain co-located large loads and some front-of-the-meter large loads to provide load flexibility during emergencies.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- Banned claims for the writer: Any power or capacity price level or forecast; Any claim that a market design favours a named company.

### `co-vistra` — Vistra: generation fleet and retail

Kind: company · Companies: VST (CIK 0001692819) · Era: 2025-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-vistra`
- [x] `co-vistra:f1` Vistra describes itself as an integrated retail electricity and power generation company.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f2` Vistra's operations are aligned into five reportable segments: Retail, Texas, East, West, and Asset Closure.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f3` At December 31, 2025, Vistra's net generating capacity was 43,641 MW, of which natural gas accounted for 26,989 MW, coal for 8,743 MW, nuclear for 6,448 MW and solar and battery for 1,274 MW.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f4` Vistra owns and operates six nuclear generation units at four facilities: Comanche Peak Units 1 and 2 in ERCOT, and Beaver Valley Units 1 and 2, Perry and Davis-Besse in PJM.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f5` Vistra's retail operations serve approximately 5 million customers, including approximately 2.6 million retail electricity customers in Texas.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f6` In September 2025, Vistra announced a 20-year power purchase agreement to supply Amazon Web Services with 1,200 MW of carbon-free power from its Comanche Peak Nuclear Power Plant.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f7` In January 2026, Vistra announced 20-year power purchase agreements with Meta Platforms to supply 2,609 MW of carbon-free power and capacity from its PJM nuclear plants.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f8` In December 2025, Vistra executed definitive agreements to acquire Cogentrix Energy, consisting of 10 natural gas generation facilities totaling approximately 5,500 MW of capacity.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- [x] `co-vistra:f9` Vistra's 10-K states that it has nuclear fuel contracted to support all of its refueling needs through 2030.  
  Source: [Vistra Corp. Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1692819/000169281926000006/vistra-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-27
- Banned claims for the writer: Any claim that the Cogentrix acquisition will close or on what timeline; Any price or dollar value of Vistra's power purchase agreements.

### `co-constellation` — Constellation Energy: nuclear fleet and contracts

Kind: company · Companies: CEG (CIK 0001868275) · Era: 2024-2026 · Category fit: blueprints, battles

- [x] **Entry signed off** → set `verified_by` / `verified_at` for `co-constellation`
- [x] `co-constellation:f1` At December 31, 2025, Constellation's owned generating resources had a total capacity of 31,676 MW, and it had contracted generation with a total capacity of 4,798 MW, figures that exclude Calpine.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f2` Constellation has five reportable segments, representing the geographic regions in which its owned generating resources are located and its customer-facing activities are conducted: Mid-Atlantic, Midwest, New York, ERCOT, and Other Power Regions.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
  ✎ Corrected 2026-10-23: the 10-K's segment definition also covers where customer-facing activities are conducted.
- [x] `co-constellation:f3` Constellation's operating revenues were $25,533 million for 2025, compared with $23,568 million for 2024.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f4` On January 7, 2026, Constellation acquired Calpine for a purchase price of approximately $22 billion, consisting of 50 million newly issued shares of its common stock and approximately $4.5 billion in cash.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f5` Constellation's 10-K states that, after divestitures connected with regulatory approvals, Calpine owns and operates natural gas, geothermal, battery storage and solar assets with approximately 23 GW of generation capacity.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f6` In September 2024, Constellation executed a 20-year power purchase agreement with Microsoft that supports the restart of Three Mile Island Unit 1, renamed the Crane Clean Energy Center, which was retired in 2019 for economic reasons.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f7` Constellation's 10-K describes the Crane site as having approximately 835 MW of emissions-free capacity once operational.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f8` In June 2025, Constellation signed a 20-year power purchase agreement with Meta Platforms for the output of the Clinton Clean Energy Center.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f9` Constellation reported nuclear capacity factors of 94.7% for 2025, 94.6% for 2024 and 94.4% for 2023, at ownership percentage.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- [x] `co-constellation:f10` Constellation's 10-K states that it operates all of its nuclear generating stations except the units at Salem and STP.  
  Source: [Constellation Energy Corporation Form 10-K for the fiscal year ended December 31, 2025](https://www.sec.gov/Archives/edgar/data/1868275/000186827526000032/ceg-20251231.htm) — U.S. Securities and Exchange Commission (EDGAR), 2026-02-24
- Banned claims for the writer: Any date by which the Crane Clean Energy Center will restart; Any price or dollar value of Constellation's power purchase agreements; Any claim about the performance of the combined company after the Calpine acquisition.

## Dropped while drafting (could not be confirmed from a primary source)

- **Palisades restart approval by the NRC (July / August 2025).** A search summary pointed to NRC News Release 25-046 (2025-07-24) and to a later NRC filing about the plant returning to operating status on 2025-08-25, but nrc.gov blocked every fetch and its PDFs would not render, so the text was never read. The entry keeps only the DOE loan fact. To add it, open https://www.nrc.gov/docs/ML2521/ML25213A079.pdf yourself.
- **LBNL 2028 scenarios** (6.7–12% of U.S. electricity, 325–580 TWh) and **Queued Up** projections: these are forecasts, which the fact rules exclude. They are listed as banned claims instead.
- **FERC's November 2024 order on the Susquehanna co-location agreement** (docket ER24-2172): FERC blocked fetches and the order was not read.
- **The 2% data-centre share of U.S. electricity** on two DOE pages (2023 and undated): it is an older figure that conflicts with LBNL's 4.4% for 2023, so it was left out.
- **NVIDIA's August 2025 H20 licences and the reported 15% revenue expectation**: the 10-K reports an expectation that no regulation codified, so it is not a settled fact.
- **Named executives' statements about internet traffic growth** in the 2004 FRBSF paper: they are statements attributed to a person, which the rules exclude.
