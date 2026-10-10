"""`price_claims` — a ticker Insights card states no share / coin price (insight_conclusion.py).

TestFlight CRWV, Tue 2026-10-06: the chip read +6.3% while the card said "CoreWeave shares
experienced a 2.2% slip" and concluded "…countered by recent price declines". Owner
decisions 2026-10-09: no price moves and no price levels on a ticker card; market cap as a
SIZE, valuation multiples and analyst price targets stay; every fundamental stays.

Built test-first against four adversarial review rounds. A naive detector flagged 45 of 240
honest lines; a narrow one missed 98 of 117 real claims; round 3 found 54 false positives
and 173 misses in the next version; round 4 found 62 false positives and 71 misses. The
tables below are those reviewers' sentences, COPIED here (their scratch files were
temporary), plus the probes written while building it:

* MUST-PASS (every HONEST_* table + the anchors): ZERO hits. Non-negotiable — a false
  positive drops an honest point or hides a whole card.
* MUST-FLAG (FLAG_* + the anchors): every row is caught.
* RECLASSIFIED_ALLOWED: reviewer must-flag rows that the owner's decisions ALLOW.
* PROMPT_ONLY: real price talk deliberately left to the prompt, asserted NOT flagged so that
  widening the detector is a conscious change. Each says why.
* FLAGGED_BY_DESIGN: lines that could be honest but share the anchor's shape; asserted
  flagged so that narrowing is a conscious change too.

Terms are what the caller passes: rounds 1-2 use the symbol + the FULL name (TERMS); round 3
rows carry the lower-case names `price_terms` sends today, head words included ("delta",
"rocket", "bank of"). test_must_pass_holds_with_production_shaped_terms re-runs every
must-pass row with the widest term set `price_terms` can produce.

Category 1 (pure, no network).
"""

from __future__ import annotations

import re
import time

import pytest

import app.services.insight_conclusion as ic
from app.services.insight_conclusion import (
    PRICE_CLAIM_PATTERNS,
    price_claims,
    price_term_patterns,
)

TERMS = {
    'ORCL': ['ORCL', 'Oracle'],
    'ETH': ['ETHUSD', 'ETH', 'Ethereum'],
    'BTC': ['BTCUSD', 'BTC', 'Bitcoin'],
    'CRWV': ['CRWV', 'CoreWeave'],
    'TGT': ['TGT', 'Target'],
    'V': ['V', 'Visa'],
    'GAP': ['GAP', 'Gap'],
    'XYZ': ['XYZ', 'Block'],
    'SNAP': ['SNAP', 'Snap'],
    'OPEN': ['OPEN', 'Opendoor'],
    'RKLB': ['RKLB', 'Rocket Lab USA'],
    'RKT': ['RKT', 'Rocket Companies'],
    'MSTR': ['MSTR', 'Strategy'],
    'NET': ['NET', 'Cloudflare'],
    'GM': ['GM', 'General Motors'],
    'AXP': ['AXP', 'American Express'],
    'FSLR': ['FSLR', 'First Solar'],
    'HD': ['HD', 'Home Depot'],
    'LYV': ['LYV', 'Live Nation Entertainment'],
    'ON': ['ON', 'onsemi'],
    'HAS': ['HAS', 'Hasbro'],
    'A': ['A', 'Agilent'],
    'IT': ['IT', 'Gartner'],
    'AI': ['AI', 'C3.ai'],
    'NOW': ['NOW', 'ServiceNow'],
    'ALL': ['ALL', 'Allstate'],
    'MU': ['MU', 'Micron Technology'],
    'NVDA': ['NVDA', 'Nvidia'],
    'AAPL': ['AAPL', 'Apple'],
    'TSLA': ['TSLA', 'Tesla'],
    'ROSE': ['ROSEUSD', 'ROSE', 'Oasis'],
    'ONE': ['ONEUSD', 'ONE', 'Harmony'],
    'TRUMP': ['TRUMPUSD', 'TRUMP', 'Official Trump'],
    'HOOD': ['HOOD', 'Robinhood Markets'],
    'ISRG': ['ISRG', 'Intuitive Surgical'],
    'AMD': ['AMD', 'Advanced Micro Devices'],
    'PSA': ['PSA', 'Public Storage'],
    'DAL': ['DAL', 'Delta Air Lines'],
    'XOM': ['XOM', 'Exxon Mobil'],
    'NFLX': ['NFLX', 'Netflix'],
    'SOL': ['SOLUSD', 'SOL', 'Solana'],
    'COIN': ['COIN', 'Coinbase Global'],
    'WMT': ['WMT', 'Walmart'],
    'BRK': ['BRK-B', 'Berkshire Hathaway'],
    'META': ['META', 'Meta Platforms'],
    'KO': ['KO', 'Coca-Cola'],
    'MSFT': ['MSFT', 'Microsoft'],
    'AMZN': ['AMZN', 'Amazon'],
    'PLTR': ['PLTR', 'Palantir'],
    'JPM': ['JPM', 'JPMorgan Chase'],
    'BAC': ['BAC', 'Bank of America'],
    'COST': ['COST', 'Costco'],
    'LLY': ['LLY', 'Eli Lilly'],
    'PFE': ['PFE', 'Pfizer'],
    'BA': ['BA', 'Boeing'],
    'INTC': ['INTC', 'Intel'],
    'SPY': ['SPY'],
    'QQQ': ['QQQ'],
    'GLD': ['GLD'],
    'GOOGL': ['GOOGL', 'Alphabet'],
    'GE': ['GE', 'General Electric'],
    'NEM': ['NEM', 'Newmont'],
    'NKE': ['NKE', 'Nike'],
    'CRM': ['CRM', 'Salesforce'],
    'XRP': ['XRPUSD', 'XRP'],
    'CRWV:sym': ['CRWV'],
    'DOGE': ['DOGEUSD', 'DOGE', 'Dogecoin'],
    'ORCL:sym': ['ORCL'],
}

# Review round 1 (2026-10-09): the repo's own corpus + fundamentals, per-share figures,
# share-of / market share, stock pay, inventories, product and commodity prices, price
# targets, and tickers that are words. (scope, text)
HONEST_ROUND1 = [
 ('ETH', 'Ethereum ETFs saw inflows while Bitcoin ETFs experienced outflows, indicating selective institutional interest.'),
 ('ETH', 'Researchers significantly reduced estimates for quantum computing attacks on Ethereum, though upgrades are still planned.'),
 ('ETH', 'Analysts project ETH could reach $11,800 by 2030, driven by fee revenue and staking yields.'),
 ('ETH', 'ETH faces mixed signals amid ETF flows and quantum risks'),
 ('ORCL', 'A US$5 billion buyback anchors the stock.'),
 ('ORCL', 'At 30x earnings the stock leaves little room.'),
 ('ORCL', 'Oracle announced a $10 billion buyback program'),
 ('ORCL', 'Oracle is expected to post 40% cloud growth in fiscal 2027, per its guidance.'),
 ('ORCL', 'Oracle granted stock options to executives after the move to Texas.'),
 ('ORCL', "A $455 billion backlog gives Oracle's cloud growth years of visible demand."),
 ('ETH', "A 3.4% staking yield keeps Ethereum's institutional appeal ahead of its risks."),
 ('ORCL', "Cloud strength at Oracle is outrunning the drag from Wall Street's rate worries."),
 ('ORCL', 'Oracle beats on cloud revenue as backlog hits a record'),
 ('ORCL', 'Oracle reported Q1 results with revenue up.'),
 ('ORCL', 'Oracle RPO hits $455 billion'),
 ('ETH', 'Staking yields near 3.4% draw funds'),
 ('ORCL', 'The beat and the target raises point the same way for Salesforce.'),
 ('ORCL', 'Free cash flow can be used for dividends, funding expansion, or buying back shares.'),
 ('ORCL', 'This leftover cash can fund dividends, growth, and share repurchases.'),
 ('ORCL', "GameStop's CEO disclosed buying GameStop stock"),
 ('ORCL', 'Revenue rose 12%.'),
 ('AAPL', 'Record services revenue is carrying the quarter while hardware lags.'),
 ('ORCL', "Earnings per share rose 18% to $1.47, ahead of the company's own guidance."),
 ('ORCL', 'Adjusted earnings per share climbed to $2.10 as cloud margins widened.'),
 ('AAPL', 'Dividend per share rose 4% after the board approved a higher payout.'),
 ('BRK', 'Book value per share climbed 12% over the year.'),
 ('AAPL', 'Diluted shares fell 3% as buybacks continued.'),
 ('AAPL', 'Outstanding shares fell 3% thanks to the $110 billion repurchase program.'),
 ('AAPL', 'Shares repurchased rose to 140 million in the quarter.'),
 ('AAPL', 'Shares bought back jumped 20% from a year earlier.'),
 ('NVDA', 'Data-center share of revenue jumped to 88% from 83%.'),
 ('AAPL', "Services' share of revenue climbed to 26%, a record."),
 ('AMD', "AMD's share of the server CPU market rose to 36%."),
 ('AMD', 'AMD continued to post share gains against Intel in servers.'),
 ('NVDA', "Nvidia's share of AI accelerator spending fell to 80% as custom chips grew."),
 ('META', 'Stock-based compensation rose 18% as hiring resumed.'),
 ('META', 'Stock based compensation rose 18% as hiring resumed.'),
 ('META', 'Stock comp fell 5% year over year.'),
 ('CRWV', 'Stock option expense rose sharply after the IPO.'),
 ('META', 'Restricted stock awards jumped 25% in the year.'),
 ('WMT', 'In-stock levels rose to their best in three years, helping traffic.'),
 ('TGT', 'Out-of-stock rates fell sharply after the supply-chain overhaul.'),
 ('XOM', 'U.S. crude stocks fell 4.5 million barrels last week, tightening supply.'),
 ('XOM', 'Distillate stocks rose 2 million barrels, the EIA said.'),
 ('ORCL', 'Analysts raised their stock price target to $250 from $210.'),
 ('ORCL', "The stock's price target climbed 15% after the RPO beat."),
 ('ORCL', 'Its share price target was raised to $250 at Jefferies.'),
 ('MU', 'Micron noted recent price declines in NAND would pressure margins next quarter.'),
 ('MU', "Memory prices rose 20% in the quarter, lifting Micron's gross margin."),
 ('MU', 'Average selling prices declined in the high-single digits.'),
 ('NFLX', 'Netflix said its price increases helped revenue grow 16%.'),
 ('TSLA', 'Tesla said its price cuts weighed on automotive gross margin.'),
 ('TSLA', 'Its price drops on the Model Y lifted deliveries but squeezed margins.'),
 ('NFLX', 'Recent price hikes on the ad-free plan lifted average revenue per member.'),
 ('WMT', 'Walmart said recent price increases from suppliers were modest.'),
 ('XOM', 'Recent price gains in crude lifted upstream earnings.'),
 ('XOM', 'Oil price declines weighed on upstream margins.'),
 ('NVDA', 'Nvidia and Foxconn share a joint venture whose output rose 30%.'),
 ('COIN', 'Coinbase sold its shares in the joint venture as trading volume jumped 40%.'),
 ('AAPL', 'Apple and Google share search revenue that climbed 10% last year.'),
 ('ORCL', 'Insiders sold 1 million shares as revenue rose 12%.'),
 ('ORCL', 'Oracle will issue new shares while debt fell 5%.'),
 ('ORCL', 'Stock buybacks across the company hit a record high in fiscal 2026.'),
 ('AAPL', "Apple's stock buybacks reached a record $110 billion."),
 ('COIN', 'The market value of its bitcoin holdings rose $2 billion in the quarter.'),
 ('BRK', "The market value of Berkshire's equity portfolio topped $300 billion."),
 ('ETH', 'Stablecoin market cap on Ethereum hit a record $180 billion.'),
 ('ORCL', 'The deal values Cerner at $28 billion including debt.'),
 ('ORCL', 'CoreWeave raised $2 billion in a share sale to fund data centers.'),
 ('ORCL', 'The IPO priced at $40, above the marketed range.'),
 ('ORCL', "A Fed rate cut would lower Oracle's borrowing costs."),
 ('ORCL', 'Treasury yields fell, easing pressure on capital-heavy cloud builders.'),
 ('BTC', 'Bitcoin ETF inflows rose 20% week over week.'),
 ('BTC', 'Bitcoin hash rate climbed 5% to a new high.'),
 ('BTC', "Bitcoin miners' revenue fell 12% after the halving."),
 ('BTC', 'Bitcoin dominance rose to 58% as altcoins lagged.'),
 ('ETH', 'Ethereum TVL fell 10% as DeFi activity cooled.'),
 ('ETH', 'Ethereum gas fees dropped 30% after the upgrade.'),
 ('ETH', "Ethereum's staking yield rose to 3.4% this month."),
 ('ETH', 'ETH ETF inflows jumped 40% this week.'),
 ('SOL', 'Solana transaction volume surged 40% on memecoin activity.'),
 ('TGT', 'Target comparable sales fell 3% as discretionary demand softened.'),
 ('TGT', "Target's digital sales jumped 10% on same-day delivery."),
 ('V', "Visa's payments volume rose 8% on a constant-currency basis."),
 ('V', 'Visa cross-border volume rose 12% as travel stayed strong.'),
 ('GAP', 'Gap comparable sales rose 3%, led by Old Navy.'),
 ('GAP', "Gap's gross margin climbed 1.2 points on lower air freight."),
 ('XYZ', 'Block gross profit rose 14% as Cash App grew.'),
 ('XYZ', "Block's Cash App monthly actives climbed 5% to 57 million."),
 ('XYZ', 'Bitcoin block rewards fell 50% at the halving, cutting miner revenue.'),
 ('SNAP', 'Snap daily active users rose 9% to 477 million.'),
 ('SNAP', 'Snap revenue climbed 10% as direct-response ads improved.'),
 ('OPEN', 'Opendoor home acquisitions fell 30% as it tightened pricing.'),
 ('RKLB', 'Rocket Lab backlog climbed 20% to $1 billion on Neutron contracts.'),
 ('RKT', 'Rocket mortgage originations jumped 25% as rates eased.'),
 ('MSTR', "Strategy's BTC Yield rose to 26% year to date."),
 ('MSTR', 'Strategy bitcoin holdings climbed 10% after the latest purchase.'),
 ('NET', 'Net revenue retention rose to 115%, its highest in two years.'),
 ('NET', 'Net new ARR jumped 30% from a year earlier.'),
 ('ON', 'Spending on chips jumped 20% as automakers restocked.'),
 ('ON', 'Tariffs on chips rose to 25% under the new rule.'),
 ('HAS', 'Revenue has climbed 10% on Wizards of the Coast licensing.'),
 ('HAS', 'Net income has fallen 5% despite higher toy sales.'),
 ('A', 'Revenue showed a sharp rise of 5% in life sciences.'),
 ('A', 'Demand for analytical instruments saw a slight dip, down 2%.'),
 ('IT', 'IT spending will climb 9.8% next year, Gartner forecast.'),
 ('AI', 'AI spending rose sharply across federal agencies.'),
 ('ALL', 'All segments rose 5% in written premium.'),
 ('NOW', 'Subscription revenue now climbs 21% a year.'),
 ('FSLR', 'First quarter revenue rose 18% on higher module volumes.'),
 ('FSLR', "First Solar's bookings climbed 2 gigawatts higher in the quarter."),
 ('HD', 'Home sales fell 4% in August, a drag on renovation demand.'),
 ('LYV', 'Live event attendance rose 8% across its venues.'),
 ('GM', 'General Motors said EV deliveries jumped 46% in the quarter.'),
 ('AXP', "American consumers' card spending rose 7%, the company said."),
 ('ROSE', 'Network fees rose and then fell 5% after the upgrade.'),
 ('ONE', 'One metric rose 5%: daily active wallets.'),
 ('TRUMP', 'Trump approval fell 3 points in the latest poll.'),
 ('TRUMP', 'Trump tariffs rose to 25% on Chinese goods.'),
 ('HOOD', 'Robinhood funded customers rose 10% to 26 million.'),
 ('HOOD', 'Robinhood trading volumes jumped 40% in equities.'),
 ('ISRG', "Intuitive's share of robotic surgeries rose to 80% in the U.S."),
 ('ISRG', 'Procedure growth surged 17% as da Vinci 5 placements rose.'),
 ('PSA', 'Public storage demand fell 2% as moving season slowed.'),
 ('DAL', 'Delta unit revenue rose 4% on premium cabins.'),
 ('AAPL', "Apple's shareholders approved the board's pay plan."),
 ('AAPL', 'Stockholders voted against the proposal.'),
 ('ORCL', "The broader stock market fell, but Oracle's backlog rose."),
 ('ORCL', 'Shareholders will share in gains from the cloud build-out.'),
 ('WMT', "Walmart's stockpile of inventory fell 2% as it cleared seasonal goods."),
 ('ORCL', 'Oracle shares a common goal with OpenAI: capacity.'),
 ('KO', 'Coca-Cola raised prices 6% and volume held steady.'),
 ('MU', 'DRAM contract prices climbed 15% in the quarter.'),
 ('WMT', 'Egg prices fell 30%, lowering grocery inflation.'),
 ('NVDA', "Nvidia's market share gains in networking continued."),
 ('ORCL', "Oracle's share of cloud infrastructure spending climbed to 6%."),
 ('COIN', "Coinbase's share of spot volume slipped to 5% as rivals cut fees."),
 ('AAPL', 'Apple stock remains popular with retail investors, a survey found.'),
 ('ORCL', 'The stock split takes effect next month.'),
 ('ORCL', 'Oracle plans a stock split after shares outstanding fell.'),
 ('NVDA', "Nvidia's share count fell for the sixth straight quarter."),
 ('ORCL', 'A $10B share buyback was approved.'),
 ('ORCL', 'Pricing power held up as customers renewed.'),
 ('ORCL', "Analysts' price target raised to $180."),
 ('ORCL', 'Gross margin fell on higher depreciation.'),
 ('ORCL', 'Backlog climbed to a record.'),
 ('AMD', "TSMC's share of advanced chip production rose to 90%."),
 ('ISRG', "Stryker's share of surgical robot installs climbed to 20%."),
 ('RKLB', 'Rolling stock orders rose 15% at Wabtec, a supplier.'),
 ('ORCL', 'Preferred stock dividends rose 5% after the reset.'),
 ('BTC', 'The stock of bitcoin held on exchanges fell 3% to a five-year low.'),
 ('OPEN', 'A rebound in the stock of homes for sale helped Opendoor buy more homes.'),
]

# Review round 2: ordinary earnings / deal / crypto-network news. (scope, text)
HONEST_ROUND2 = [
 ('NVDA', 'Nvidia guided fourth-quarter revenue to $65 billion, above analyst estimates.'),
 ('NVDA', 'Blackwell shipments ramped faster than expected, lifting data-center sales 66%.'),
 ('NVDA', 'Gross margin dipped to 72.4% as the new platform ramped.'),
 ('AAPL', 'iPhone revenue climbed 6% on strong demand in China.'),
 ('AAPL', 'Apple approved a $100 billion stock repurchase authorization.'),
 ('AAPL', 'The board raised the quarterly dividend 4% to $0.27 per share.'),
 ('AAPL', 'Services revenue hit a record $28 billion, up 14%.'),
 ('AAPL', 'Apple faces an EU fine over App Store steering rules.'),
 ('MSFT', 'Azure growth slowed to 33% as capacity constraints persisted.'),
 ('MSFT', "Microsoft's commercial bookings jumped 112% on an OpenAI commitment."),
 ('AMZN', 'AWS operating margin fell to 33% on higher depreciation.'),
 ('AMZN', 'Amazon plans to cut 14,000 corporate jobs to reduce layers.'),
 ('TSLA', 'Tesla deliveries rose 7% to 497,000 vehicles in the quarter.'),
 ('TSLA', "Shareholders approved Musk's pay package at the annual meeting."),
 ('TSLA', 'Energy storage deployments doubled to 12.5 GWh.'),
 ('ORCL', 'Remaining performance obligations jumped 359% to $455 billion.'),
 ('ORCL', 'Oracle sold $18 billion of bonds to fund data-center construction.'),
 ('ORCL', "Moody's revised Oracle's outlook to negative on rising debt."),
 ('CRWV', 'CoreWeave signed a $14 billion contract with Meta for AI capacity.'),
 ('CRWV', "CoreWeave's backlog rose to $55.6 billion after new OpenAI deals."),
 ('CRWV', 'Insiders sold shares under prearranged trading plans.'),
 ('CRWV', 'The Core Scientific acquisition was rejected by its shareholders.'),
 ('CRWV', 'Interest expense more than doubled as debt financing rose.'),
 ('CRWV', 'Analysts at Jefferies upgraded the stock to buy with a $180 price target.'),
 ('CRWV', 'Lockup expiration will allow early holders to sell more shares.'),
 ('CRWV', 'Capital expenditure guidance fell to $12-14 billion on data-center delays.'),
 ('PLTR', 'US commercial revenue surged 121% as AIP adoption widened.'),
 ('PLTR', 'Palantir raised its full-year revenue outlook to $4.4 billion.'),
 ('JPM', 'Net interest income rose 2% as deposit costs eased.'),
 ('JPM', 'JPMorgan set aside $3.4 billion for credit losses.'),
 ('JPM', 'Investment banking fees climbed 16% on a rebound in deal-making.'),
 ('BAC', 'Trading revenue jumped 15% on volatile markets.'),
 ('XOM', "Exxon's Permian output rose to a record 1.6 million barrels a day."),
 ('XOM', 'Lower oil prices cut upstream earnings by $1.2 billion.'),
 ('KO', "Coca-Cola's unit case volume rose 1% as price/mix added 6%."),
 ('COST', 'Costco raised its membership fee for the first time since 2017.'),
 ('COST', 'Comparable sales rose 5.7% excluding gasoline and currency.'),
 ('NFLX', 'Netflix will stop reporting subscriber numbers starting next year.'),
 ('NFLX', 'Ad-tier memberships rose 59% quarter over quarter.'),
 ('WMT', 'Walmart cut prices on thousands of items to hold traffic.'),
 ('WMT', 'E-commerce sales rose 22% as delivery speeds improved.'),
 ('LLY', 'Mounjaro sales more than doubled to $5.2 billion.'),
 ('LLY', 'The FDA approved orforglipron for chronic weight management.'),
 ('PFE', 'Pfizer cut its full-year guidance on lower Covid product sales.'),
 ('BA', "Boeing's 737 production rose to 38 jets a month under FAA oversight."),
 ('BA', "The machinists' strike ended after members ratified a new contract."),
 ('INTC', "Intel's foundry losses narrowed to $2.3 billion."),
 ('INTC', 'The US government took a 10% stake in Intel through a share sale.'),
 ('INTC', 'Intel issued 433 million new shares to the government.'),
 ('MU', 'HBM revenue rose sharply as AI servers ramped.'),
 ('MU', 'Micron said DRAM prices rose in the high-teens percentage range.'),
 ('AMD', 'AMD signed a multi-year deal to supply OpenAI with 6 gigawatts of GPUs.'),
 ('AMD', 'OpenAI received warrants for up to 160 million AMD shares.'),
 ('BTC', 'Spot bitcoin ETFs drew $3.2 billion of inflows over the week.'),
 ('BTC', 'Corporate treasuries added 100,000 BTC in the quarter.'),
 ('BTC', "The network's hashrate set a record above 1 zettahash."),
 ('ETH', 'The Fusaka upgrade cut layer-2 data costs.'),
 ('ETH', 'Staked ETH rose to 35 million, nearly 29% of supply.'),
 ('ETH', "Validators' exit queue climbed to 2.5 million ETH."),
 ('SOL', 'Solana ETF approvals broadened institutional access.'),
 ('SOL', 'Network revenue fell 20% as memecoin trading cooled.'),
 ('COIN', "Coinbase's transaction revenue fell 5% on lower retail volume."),
 ('COIN', 'Subscription and services revenue climbed 9% on USDC balances.'),
 ('HOOD', 'Robinhood added to the S&P 500 index.'),
 ('HOOD', 'Net deposits rose to $20 billion in the month.'),
 ('MSTR', 'Strategy bought 10,000 bitcoin funded by a stock sale.'),
 ('MSTR', 'Strategy sold preferred stock to fund more bitcoin purchases.'),
 ('MSTR', "The company's mNAV fell below 1.5x, limiting at-the-market stock issuance."),
 ('SPY', "The ETF's expense ratio stays at 0.0945%."),
 ('SPY', 'Fund assets rose to $650 billion on record inflows.'),
 ('QQQ', 'Technology weighting in the index climbed to 61%.'),
 ('GLD', 'Gold holdings in the trust rose 12 tonnes as central banks kept buying.'),
 ('TGT', 'Target cut its full-year sales outlook as discretionary spending weakened.'),
 ('TGT', 'Target will invest $5 billion in stores and supply chain next year.'),
 ('V', 'Visa agreed to settle merchant claims over swipe fees.'),
 ('SNAP', 'Snap signed a deal with Perplexity worth $400 million.'),
 ('XYZ', 'Block will lay off 900 employees to streamline Cash App.'),
 ('OPEN', 'Opendoor named a new chief executive after an activist campaign.'),
 ('GAP', 'Gap Inc. said tariffs would cost up to $300 million.'),
 ('ORCL', 'Oracle shares a data-center site with OpenAI in Abilene, Texas.'),
 ('ORCL', 'Stock compensation expense rose to $1.2 billion.'),
 ('ORCL', 'The share count rose 1% because of employee stock grants.'),
 ('ORCL', 'Its share of the database market fell slightly to 28%.'),
 ('META', "Meta's share of digital ad spending rose to 22%."),
 ('META', 'Family daily active people rose 8% to 3.5 billion.'),
 ('META', 'Reality Labs losses climbed to $4.4 billion.'),
 ('GOOGL', "Search's share of Alphabet revenue fell to 55% as cloud grew."),
 ('GOOGL', 'Waymo paid rides doubled to 250,000 a week.'),
]

# The under-block reviewer's must-NOT-flag twins. (scope, text)
HONEST_UNDERBLOCK_PASS = [
 ('CRWV', "CoreWeave's pricing power improved as demand outpaced supply."),
 ('CRWV', 'Analysts raised the price target to $180.'),
 ('CRWV', 'Revenue rose 20% year over year.'),
 ('CRWV', 'Backlog climbed to a record $55 billion.'),
 ('CRWV', 'Gross margin fell to 70%.'),
 ('CRWV', 'CoreWeave gained market share in AI cloud.'),
 ('CRWV', 'The board approved a $10B share buyback.'),
 ('CRWV', 'Shares outstanding fell after the buyback.'),
 ('CRWV', 'Stock-based compensation rose 30%.'),
 ('CRWV', 'Oil price declines weighed on margins.'),
 ('CRWV', 'Insiders sold 1 million shares in September.'),
 ('CRWV', 'The company announced a 4-for-1 stock split.'),
 ('CRWV', 'The company plans to issue new shares to fund data centers.'),
 ('CRWV', 'GPU rental price declines pressured margins.'),
 ('CRWV', 'H100 rental prices fell 20% this year.'),
 ('CRWV', 'Netflix raised its price to $17.99 a month.'),
 ('CRWV', 'Adjusted EPS rose to $0.45 per share.'),
 ('CRWV', 'The dividend rose to $0.50 per share.'),
 ('CRWV', 'The deal closed at $2 billion.'),
 ('CRWV', 'CoreWeave closed the deal at $2 billion.'),
 ('CRWV', 'The company priced its offering at $40 per share.'),
 ('CRWV', 'Revenue hit a record high of $5 billion.'),
 ('ETH', 'Ethereum gained traction among developers.'),
 ('BTC', "Bitcoin miners' revenue fell 10% after the halving."),
 ('ETH', 'Network activity on Ethereum rose 15%.'),
 ('CRWV', 'Pricing pressure weighed on margins.'),
 ('CRWV', 'CoreWeave narrowed its net loss to $50 million.'),
 ('CRWV', 'Trading revenue rose 12%.'),
 ('CRWV', 'Revenue is up 40% year to date.'),
 ('CRWV', 'Sales gains of 12% this year beat guidance.'),
 ('CRWV', 'Double-digit revenue growth continued.'),
 ('CRWV', 'Utilization ran 20% below its peak capacity.'),
 ('CRWV', 'CoreWeave swung to a profit in the quarter.'),
 ('CRWV', 'CoreWeave advanced its AI plans with a new data center.'),
 ('CRWV', "The company's backlog jumped 80% to $30 billion."),
 ('CRWV', 'Momentum in AI bookings continued.'),
 ('CRWV', 'Demand for GPUs rose sharply.'),
 ('CRWV', 'CoreWeave raised $2 billion in debt at a 9% rate.'),
 ('CRWV', 'Nvidia owns about 7% of the stock.'),
 ('CRWV', 'Share count rose 5% after the note conversion.'),
 ('CRWV', 'CoreWeave gained 3% share of the GPU cloud market.'),
 ('CRWV', 'CoreWeave lost 10% of its capacity to an outage.'),
 ('CRWV', "Microsoft's stake rose to 7%."),
 ('CRWV', 'The stock award vests over four years.'),
 ('CRWV', 'Stock options grants rose 12%.'),
 ('CRWV', 'The company bought back stock worth $500 million.'),
 ('CRWV', 'Its best quarter for bookings since 2021 lifted guidance.'),
 ('CRWV', 'A 10% pullback in capex spending pressured suppliers.'),
 ('CRWV', 'Revenue grew at a double-digit pace.'),
 ('CRWV', 'Double-digit declines in sales continued.'),
 ('CRWV', 'The convertible notes convert at $150 per share.'),
 ('CRWV', 'The losing bidder walked away.'),
 ('BTC', 'Bitcoin ETF inflows rose to $1.2 billion.'),
 ('BTC', 'Bitcoin mining difficulty hit an all-time high.'),
 ('ETH', "Ethereum's staking yield fell to 3%."),
 ('ETH', 'Ethereum fees dropped 90% after the upgrade.'),
 ('ETH', 'Ethereum fees dropped to $0.01 per transfer.'),
 ('SOL', "Solana's network outage lasted five hours."),
 ('CRWV', 'CoreWeave secured a $14 billion deal with Meta.'),
 ('CRWV', "CoreWeave's revenue jumped 200% to $1.2 billion."),
 ('CRWV', 'The selloff in GPU rental rates hurt margins.'),
 ('CRWV', 'Investors weighed debt levels against record backlog.'),
 ('CRWV', "CoreWeave's stock-based compensation fell 5%."),
 ('CRWV', 'Stock compensation expense rose 12%.'),
 ('CRWV', "The company's shares outstanding rose 4%."),
 ('CRWV', 'Market share gains continued in Europe.'),
 ('CRWV', 'CoreWeave rose to prominence after the ChatGPT launch.'),
 ('CRWV', 'CoreWeave climbed into the top tier of AI clouds.'),
 ('CRWV', 'CoreWeave jumped at the chance to buy Core Scientific.'),
 ('CRWV', "OpenAI's valuation rose to $500 billion in its funding round."),
 ('CRWV', 'The deal added $2 billion to its revenue backlog.'),
 ('CRWV', 'Peers outperformed on revenue growth.'),
 ('CRWV', 'Microsoft accounted for 62% of revenue.'),
 ('CRWV', 'Sales rose 5% in early 2026.'),
 ('CRWV', 'The company boosted its stock buyback to $5 billion.'),
 ('CRWV', 'Selling, general and administrative costs rose 9%.'),
 ('ETH', 'The upgrade drove adoption among developers.'),
]

# Written while building the detector: each probes one guard (a preposition before the name,
# a quantity word, a forecast / condition, 'shares' as a verb, the stock as an object,
# product prices, 'doubled its …'). (scope, text)
HONEST_PROBES = [
 # their twins (stockpiles, business pressure):
 ('CRWV', 'Crude stocks fell by 3 million barrels last week.'),
 ('CRWV', 'Distillate stocks rose last week.'),
 ('CRWV', 'Fish stocks declined in the North Sea.'),
 ('CRWV', 'Copper stocks in LME warehouses fell.'),
 ('CRWV', 'Housing stocks rose as builders finished more homes.'),
 ('CRWV', 'The company faces pricing pressure from rivals.'),
 ('CRWV', 'Margins are under pressure from higher power costs.'),
 # their twins:
 ('CRWV', 'Margins have been in a downward trend since 2024.'),
 ('CRWV', 'Declining stock performance awards were cut from the plan.'),
 ('CRWV', 'The shortage caused stocks of copper to fall.'),
 ('CRWV', 'Demand for GPUs is in a downward trend.'),
 ('WMT', 'The recall caused inventory stocks to fall.'),
 # twins of the close / sector rows (2026-10-09):
 ('CRWV', 'The company closed down its Texas plant.'),
 ('CRWV', 'CoreWeave closed down two older data centers last year.'),
 ('CRWV', 'Sector demand surged in the quarter.'),
 ('CRWV', 'Sector revenue slumped 5%.'),
 ('CRWV', 'Revenue ended up 3% higher than guidance.'),
 ('CRWV', 'The deal closed 2% above the first offer.'),
 # Review 2026-10-09 twins (a steady dividend, index changes, the STOCK Act, reinvested profits, GLD flows):
 ('V', 'Visa shares offer a steady dividend yield.'),
 ('KO', 'Coca-Cola raised its dividend 5%, and its shares offer a steady income stream.'),
 ('KO', 'The stock pays a steady dividend that Coca-Cola raised for a 63rd straight year.'),
 ('INTC', "Intel's stock was dropped from the Dow Jones Industrial Average and replaced by Nvidia."),
 ('INTC', 'Intel stock lost its spot in the Dow after 25 years.'),
 ('HOOD', 'The stock gained a spot in the S&P 500 in September.'),
 ('NVDA', 'Disclosures under the STOCK Act lag trades by as much as 45 days.'),
 ('NVDA', 'STOCK Act reports lag trades by up to 45 days, so the purchase may be weeks old.'),
 ('AMZN', 'For years it took profits it could have paid out and reinvested them into lower prices.'),
 ('AMZN', 'Amazon takes profits it could return to shareholders and reinvests them in AI data centers.'),
 ('GLD', 'Holdings of SPDR Gold Shares rose 0.4% to 875 tonnes, the most since 2022.'),
 ('GLD', 'Inflows into SPDR Gold Shares jumped 20% in September.'),
 ('ORCL', 'A 10% drop in trading revenue hit the bank.'),
 ('ORCL', 'The recall caused inventory to fall.'),
 ('ORCL', 'The plan caused stock options to vest early.'),
 ('ORCL', 'Demand led shareholders to approve the merger.'),
 ('ORCL', 'The rule change caused stock buybacks to slow.'),
 ('CRWV', 'Stock performance awards vest in 2027.'),
 ('CRWV', 'Weak stock performance metrics cut the executive bonus pool.'),
 ('CRWV', 'Strong stock performance units were granted to the new CFO.'),
 ('CRWV', 'The plan ties pay to relative stock performance against peers.'),
 ('CRWV', 'Insiders sold shares in advance of the earnings report.'),
 ('ORCL', 'Oracle shares a slide deck with investors every quarter.'),
 ('CRWV', 'Revenue at CoreWeave rose 200%.'),
 ('CRWV', 'CoreWeave declined to comment.'),
 ('CRWV', 'A spokesperson for CoreWeave declined, citing company policy.'),
 ('CRWV', 'CoreWeave jumped on the opportunity to buy Core Scientific.'),
 ('CRWV', 'CoreWeave surged ahead of rivals in GPU capacity.'),
 ('INTC', "Intel's decline as a chip leader continued."),
 ('CRWV', "CoreWeave's rise to prominence came fast."),
 ('KO', "The stock's dividend yield rose to 3%."),
 ('NFLX', "Netflix's price rose to $17.99 a month."),
 ('CRWV', 'Revenue is 10% below its 2022 peak.'),
 ('ORCL', 'Oracle extended its gains in cloud market share.'),
 ('INTC', 'Intel pared losses to $2.3 billion in the foundry unit.'),
 ('BTC', 'Bitcoin ETFs saw their largest single-day inflow.'),
 ('CRWV', "It was the company's best week for bookings since 2021."),
 ('TSLA', 'Tesla had its best month in China.'),
 ('COIN', "Volatile trading lifted Coinbase's transaction revenue."),
 ('HOOD', "Heavy trading volumes boosted Robinhood's revenue."),
 ('GOOGL', 'The token count rose 30% as Gemini usage grew.'),
 ('MSFT', 'Token prices for GPT models fell 80%.'),
 ('MSFT', 'OpenAI cut the price per token by 80%.'),
 ('CRWV', 'Analysts expect shares to rise 20% to their $180 target.'),
 ('NVDA', 'The board approved a stock split, and shares will begin trading on a split-adjusted basis.'),
 ('GE', 'Shares of the spin-off will be distributed in March.'),
 ('CRWV', 'CoreWeave sold 1 million shares at $40.'),
 ('CRWV', 'The offering priced 50 million shares at $40, the top of the range.'),
 ('CRWV', 'Shares were sold at $40 in the IPO.'),
 ('CRWV', 'Shares were priced at $40.'),
 ('CRWV', 'CoreWeave is valued at $70 billion.'),
 ('CRWV', 'CoreWeave is now worth $70 billion.'),
 ('NVDA', "Nvidia's market cap is $4 trillion."),
 ('CRWV', 'The market value of its stake rose to $5 billion.'),
 ('BTC', "Bitcoin's network hashrate hit an all-time high."),
 ('NEM', "Gold prices hit a record high, lifting Newmont's margins."),
 ('MU', 'Memory chip prices rallied 20% this quarter.'),
 ('MU', 'DRAM prices staged a rally in the quarter.'),
 ('ORCL', 'The relief rally in Treasuries lowered borrowing costs.'),
 ('ORCL', 'A rally in bond markets helped Oracle refinance.'),
 ('CRWV', 'Despite the selloff in GPU rental rates, margins held.'),
 ('MU', 'Margins were hurt by price declines.'),
 ('MU', 'Despite price declines, unit volume grew.'),
 ('TSLA', 'Its price cuts weighed on margins.'),
 ('MU', 'Average selling price gains offset weaker volumes.'),
 ('BTC', 'Analysts see Bitcoin reaching $200,000 by 2026.'),
 ('CRWV', 'The notes convert if shares trade above $150.'),
 ('BTC', 'If Bitcoin falls below $50,000, miners may sell.'),
 ('CRWV', 'Shares tied to the deal will vest in 2027.'),
 ('AAPL', 'The company bought back shares worth $2 billion, lifting EPS 10%.'),
 ('AAPL', 'The company repurchased 5 million shares, down from 8 million a year earlier.'),
 ('AAPL', 'Share repurchases rose 20%.'),
 ('AAPL', 'Stock repurchases rose 20%.'),
 ('AAPL', 'Its stock is a component of the S&P 500.'),
 ('HOOD', 'The stock was added to the S&P 500 index.'),
 ('CRWV', 'The stock was upgraded to buy at Jefferies.'),
 ('AAPL', "The stock's price-to-earnings ratio fell to 30."),
 ('CRWV', 'The deal values the company at $28 billion.'),
 ('CRWV', 'CoreWeave was valued at $23 billion in its IPO.'),
 ('CRWV', 'Shares in the joint venture were split evenly.'),
 ('NFLX', 'Netflix doubled its ad tier.'),
 ('TSLA', 'Tesla doubled down on robotaxis.'),
 ('TSLA', 'Tesla doubled, then tripled, its battery output.'),
 ('CRWV', 'CoreWeave dropped out of the deal.'),
 ('TSLA', 'Tesla fell short of delivery estimates.'),
 ('ORCL', 'Oracle fell behind Microsoft in cloud share.'),
 ('ORCL', 'Oracle fell to third place in cloud.'),
 ('ORCL', 'Oracle rose to the challenge.'),
 ('ORCL', 'Oracle climbed to the top of the rankings.'),
 ('TSLA', 'Tesla slid into second place in EV sales.'),
 ('ETH', 'Ethereum gained ground among developers.'),
 ('ORCL', 'Oracle dipped into its cash reserves.'),
 ('BTC', 'Bitcoin fell out of favor with retail traders.'),
 ('ORCL', 'Oracle surged past Google in cloud bookings.'),
 ('ORCL', 'Oracle rose in the rankings.'),
 ('AMD', 'AMD gained share against Intel.'),
 ('BTC', "Bitcoin's rise as a reserve asset continued."),
 ('NVDA', "Nvidia's slide deck showed a $500 billion pipeline."),
 ('NVDA', "Nvidia's strong performance continued."),
 ('NVDA', "Nvidia's steady rise in data-center share continued."),
 ('CRWV', "CoreWeave's heavy losses widened in the quarter."),
 ('XOM', "Exxon's 5% production rise lifted earnings."),
 ('AAPL', "Apple's 5% revenue decline was driven by China."),
 ('AAPL', "Apple's 5% decline in revenue was driven by China."),
 ('NVDA', 'Data-center revenue posted a 66% gain.'),
 ('NVDA', 'Gross margin saw a 2% decline.'),
 ('NVDA', 'Earnings rose 10%, a double-digit gain.'),
 ('NKE', 'Sales posted double-digit declines.'),
 ('CRWV', 'The company posted its best quarter since 2021.'),
 ('DAL', 'It was the worst day for airline bookings since 2020.'),
 ('WMT', "Black Friday was Walmart's best day ever."),
 ('WMT', "Black Friday was Walmart's best day ever for online sales."),
 ('CRM', 'A sell-off in software stocks hurt sentiment.'),
 ('CRWV', 'The rally in AI stocks lifted valuations.'),
 ('MU', 'Meanwhile, a rally in memory prices helped margins.'),
 ('XOM', "The plunge in crude prices cut Exxon's earnings."),
 ('CRWV', 'A plunge in demand hit sales.'),
 ('CRWV', 'Revenue rose 5% on the day of the launch.'),
 ('SNAP', 'Downloads jumped 30% overnight after the launch.'),
 ('SNAP', 'Revenue per user rose 5%.'),
 ('CRWV', 'Share-based compensation fell.'),
 ('CRWV', 'Common shares outstanding fell.'),
 ('CRWV', 'CoreWeave rose 200% in revenue.'),
 ('ORCL', 'Oracle gained 2 points of market share.'),
 ('COIN', 'Coinbase gained 5% market share in derivatives.'),
 ('TSLA', 'Tesla cut prices 5%.'),
 ('TSLA', 'Tesla dropped prices 5% in China.'),
 ('TSLA', 'Tesla dropped 5% of its workforce.'),
 ('INTC', 'Intel shed 15% of its workforce.'),
 ('NFLX', 'Netflix lost 1 million subscribers.'),
 ('AAPL', 'Apple lost 5% share in China.'),
 ('WMT', 'Walmart rose 2% in same-store sales.'),
 ('BTC', 'Bitcoin rose to 58% of crypto market cap.'),
 ('BTC', 'Bitcoin climbed to 58% dominance.'),
 ('TGT', 'Target hit a record high in digital sales.'),
 ('V', 'Visa set a record high for payment volume.'),
 ('NVDA', 'Nvidia set a new record for quarterly revenue.'),
 ('ORCL', 'Oracle closed the acquisition at $28 billion.'),
 ('ORCL', 'Oracle closed at $28 billion in bookings.'),
 ('NFLX', "The price of Netflix's standard plan rose to $17.99."),
 ('BTC', "Bitcoin's price target was raised to $150,000."),
 ('ETH', "Ethereum's gas price fell to 1 gwei."),
 ('ETH', 'Ether fees fell 5%.'),
 ('ETH', 'Staked ETH rose 5% to 35 million.'),
 ('BTC', 'Exchange-held BTC fell 3% to a five-year low.'),
 ('SNAP', 'Snap shares data with advertisers.'),
 ('ORCL', 'Oracle shares its outlook with investors on Tuesday.'),
 ('AMD', 'AMD shares the market with Intel.'),
 ('SPY', 'ETF shares outstanding rose 5% on inflows.'),
 ('SPY', 'Creation units rose as the fund issued new shares.'),
 ('SPY', "SPY's assets rose to $650 billion."),
 ('SPY', "SPY's expense ratio fell to 0.09%."),
 ('WMT', 'Stores stock the item at $5 each.'),
 ('WMT', 'Retailers stock up on inventory before the holidays.'),
 ('CRWV', 'Stock-based compensation dropped 5%.'),
 ('CRWV', 'Higher rates weighed on the stock market.'),
 ('AAPL', 'Tariffs weighed on stock buybacks.'),
 ('TGT', 'Supply-chain issues hit in-stock rates.'),
 ('AAPL', 'Market share slipped to 18%.'),
 ('CRWV', 'Pricing pressure eased.'),
 ('CRWV', 'Price competition intensified in GPUs.'),
 ('CRWV', 'Investors weighed on whether the deal makes sense.'),
 ('NKE', 'Margins took a beating from tariffs.'),
 ('ORCL', 'Cloud revenue has been on a tear.'),
 ('CRWV', 'CoreWeave has been on a roll with new contracts.'),
 ('ORCL', 'Oracle took a hit to margins from depreciation.'),
 ('CRWV', 'The company lost 10% of its capacity to an outage.'),
 ('CRWV', 'The company gained 4% share of the cloud market.'),
 ('JPM', "The bank's net interest income rose 2%."),
 ('BTC', 'Strategy bought 10,000 BTC.'),
 ('BTC', 'Corporate treasuries bought more BTC as ETF inflows rose.'),
 ('HOOD', 'Robinhood rose to the S&P 500.'),
 ('NVDA', 'Nvidia rose to become the most valuable chip designer.'),
 ('CRWV', 'Its valuation of 30 times sales leaves little room.'),
 ('CRWV', 'CoreWeave trades at 25 times forward sales.'),
 ('CRWV', 'The stock trades at a premium to peers.'),
 ('CRWV', 'At $40 a share, the IPO raised $1.5 billion.'),
 ('CRWV', 'The stock award vests over four years.'),
 ('CRWV', 'Investors cheered the deal.'),
 ('ON', 'ON Semiconductor rose to prominence in silicon carbide.'),
 ('NOW', 'NOW Platform subscriptions rose 21%.'),
 ('IT', 'IT budgets rose 9%.'),
 ('A', 'A rise of 5% in bookings helped.'),
 ('AI', 'AI spending surged after the launch.'),
 ('MU', "Strong price gains lifted Micron's margins."),
 ('MU', 'Memory prices staged a rally.'),
 ('XOM', "Oil staged a rally, lifting Exxon's earnings."),
 ('INTC', "The slump hurt Intel's PC sales."),
 ('XOM', 'The plunge in crude hurt margins.'),
 ('HOOD', 'Intraday gains in volume were strong.'),
 ('CRWV', 'Trading volume in the stock tripled.'),
 ('CRWV', 'Institutional ownership of the stock rose to 70%.'),
 ('CRWV', 'Short interest in the shares rose to 25% of the float.'),
 ('CRWV', 'Demand for the shares rose in the offering.'),
 ('CRWV', 'Bets on the stock rose ahead of the report.'),
 ('CRWV', 'Analysts at Morgan Stanley raised their price target to $200, citing AI demand.'),
 ('CRWV', 'Jefferies upgraded the stock to buy, setting a $180 target.'),
 ('CRWV', 'The convertible notes are convertible into shares at $150.'),
 ('CRWV', "Nvidia holds about 7% of CoreWeave's shares."),
 ('CRWV', 'Options traders bet on a big move after earnings.'),
 ('CRWV', "The stock's implied volatility rose to 80%."),
 ('CRWV', 'CoreWeave gained a new customer.'),
 ('CRWV', 'CoreWeave gained, then lost, a major customer.'),
 ('CRWV', 'CoreWeave dropped, then reinstated, its guidance.'),
 ('ETH', 'Ethereum rose in popularity among developers.'),
 ('SOL', 'Solana jumped to second place in DEX volume.'),
 ('XRP', 'XRP spiked in usage after the ruling.'),
 ('ORCL', 'Oracle Shares Its Vision For AI Data Centers'),
 ('AAPL', 'Apple Stock Split Explained'),
 ('TSLA', 'Tesla Stock Price Target Raised At Wedbush'),
 ('ORCL', 'Is Oracle Stock A Buy After Its Cloud Deal?'),
 ('CRWV', 'The company climbed 3% in the rankings of cloud vendors.'),
 ('JPM', 'The bank gained 2% in deposits.'),
 ('ORCL', 'Oracle issued shares, helping revenue rise 5%.'),
 ('TGT', "Analysts' target rose to $250 after the beat."),
 ('ETH', "Ethereum's 2026 roadmap, priced at $5 billion, was approved."),
 ('ORCL', 'The beat lifted the stock price target at Jefferies to $250.'),
 ('AAPL', 'Shares in circulation fell 2% after the buyback.'),
 ('TSLA', 'The stock component of executive pay fell 10%.'),
 ('CRWV', 'The number of shares short rose to 30 million.'),
 ('CRWV', 'Short interest as a percentage of shares rose to 12%.'),
 ('WMT', 'Items in stock rose 5% before the holidays.'),
 ('MSTR', 'Issuance of stock rose to $5 billion.'),
 ('HOOD', 'Stock trading volumes rose 40% in the quarter.'),
 ('HOOD', 'Stock token volume jumped 50% in Europe.'),
 ('CRWV', 'Stock analysts raised their targets after the beat.'),
 ('ORCL', 'Shares are now trading at about 30 times earnings.'),
 ('ORCL', 'The shares trade at 25x forward earnings, below the sector average.'),
 ('KO', 'The stock yields 3.2%.'),
 ('KO', "The stock's dividend yield is 3%."),
 ('CRWV', 'CoreWeave stock carries a $50 billion market value.'),
 ('CRWV', 'With a market cap near $50 billion, CoreWeave is now a large-cap company.'),
 ('BTC', "Bitcoin's market cap is about $1.2 trillion."),
 ('ORCL', "Oracle's reaction to the lawsuit was swift."),
]

# The under-block reviewer's must-flag set, minus RECLASSIFIED_ALLOWED and PROMPT_ONLY below.
# (family, scope, text) — A shares/stock subject, B price nouns, C the company as subject, D
# crypto, E levels / valuation moves, F subject-less idioms.
FLAG_UNDERBLOCK = [
 ('A', 'CRWV', 'CoreWeave shares experienced a 2.2% slip.'),
 ('A', 'CRWV', 'Shares of CoreWeave rose 6% on the Microsoft deal.'),
 ('A', 'CRWV', 'The stock has more than doubled this year.'),
 ('A', 'CRWV', 'Shares are trading lower after the debt offering.'),
 ('A', 'CRWV', 'CoreWeave stock jumped after the earnings beat.'),
 ('A', 'CRWV', 'Shares of the AI cloud provider slipped after the filing.'),
 ('A', 'CRWV', 'The stock, already down 40% from its peak, faces more pressure.'),
 ('A', 'CRWV', 'Shares, which fell 30% last month, recovered some ground.'),
 ('A', 'CRWV', "CoreWeave's shares edged up on the news."),
 ('A', 'CRWV', 'Shares were higher in early trading.'),
 ('A', 'CRWV', 'The stock has tripled since its March IPO.'),
 ('A', 'CRWV', 'Shares have halved from their June peak.'),
 ('A', 'CRWV', 'Shares swung between gains and losses.'),
 ('A', 'CRWV', 'The stock was flat despite the upgrade.'),
 ('A', 'CRWV', 'Shares rose six percent on the deal.'),
 ('A', 'CRWV', 'Its stock is up 150% over the past year.'),
 ('A', 'CRWV', 'The shares remained volatile after the report.'),
 ('A', 'CRWV', 'Shares bounced back after the sell-off.'),
 ('A', 'CRWV', 'CoreWeave stock has underperformed the S&P 500 this year.'),
 ('A', 'CRWV', 'Shares have outperformed the Nasdaq since the IPO.'),
 ('A', 'CRWV', 'Its ADRs fell 4% in New York trading.'),
 ('A', 'CRWV', 'The stock hit a record high on Tuesday.'),
 ('A', 'CRWV', 'Shares closed at a record $145.'),
 ('A', 'CRWV', 'Shares steadied after two weeks of losses.'),
 ('A', 'CRWV', 'Shares rose 6 percent after the announcement.'),
 ('A', 'CRWV', 'Shares (+6%) led AI infrastructure names.'),
 ('A', 'CRWV', 'CoreWeave Stock Slides as Debt Concerns Mount'),
 ('B', 'CRWV', 'The strategy was countered by recent price declines.'),
 ('B', 'CRWV', 'The deal is overshadowed by sharp price declines.'),
 ('B', 'CRWV', 'Contract wins are offset by price weakness.'),
 ('B', 'CRWV', 'Backlog gains were countered by recent sharp price declines.'),
 ('B', 'CRWV', "The stock's sharp run-up raises the bar for the next report."),
 ('B', 'CRWV', "The stock's 40% surge reflects AI demand."),
 ('B', 'CRWV', 'A 2.2% slip in CoreWeave shares followed the filing.'),
 ('B', 'CRWV', 'The sell-off in CoreWeave stock deepened.'),
 ('B', 'CRWV', 'Its share-price decline reflects debt concerns.'),
 ('B', 'CRWV', 'The stock-price drop followed the lockup expiry.'),
 ('B', 'CRWV', 'Its recent price action reflects concerns about debt.'),
 ('B', 'CRWV', "The shares' slide continued into a third week."),
 ('B', 'CRWV', 'Recent stock weakness reflects financing worries.'),
 ('B', 'CRWV', 'Its share price has been under pressure.'),
 ('B', 'CRWV', "Strong bookings contrast with the stock's recent decline."),
 ('B', 'CRWV', 'The decline in its share price reflects debt worries.'),
 ('C', 'CRWV', 'CoreWeave Gains 4% on New Microsoft Deal'),
 ('C', 'CRWV', 'CoreWeave Rallies on Microsoft Deal'),
 ('C', 'CRWV', 'CoreWeave slides as investors weigh its debt load.'),
 ('C', 'CRWV', "CoreWeave's 2.2% slip came after a downgrade."),
 ('C', 'CRWV', 'CoreWeave sank to a three-month low.'),
 ('C', 'CRWV', 'CoreWeave hit an all-time high on Monday.'),
 ('C', 'CRWV', 'CRWV +6% after the Nvidia stake disclosure.'),
 ('C', 'CRWV', 'CoreWeave (CRWV) rose 6 percent.'),
 ('C', 'CRWV', 'CoreWeave was down 5% premarket.'),
 ('C', 'CRWV', 'CoreWeave has more than doubled since its IPO.'),
 ('C', 'CRWV', 'CoreWeave, down 40% from its high, announced a buyback.'),
 ('C', 'CRWV', 'The AI cloud provider gained 4% on the news.'),
 ('C', 'CRWV', 'The chipmaker fell sharply after the guidance.'),
 ('C', 'CRWV', 'CoreWeave jumped 6% to $145.'),
 ('C', 'CRWV', 'CoreWeave climbed as much as 9% intraday.'),
 ('D', 'BTC', 'Bitcoin fell 3% as ETF outflows mounted.'),
 ('D', 'BTC', 'Bitcoin dipped below $60,000.'),
 ('D', 'ETH', 'ETH rallied after the upgrade.'),
 ('D', 'ETH', 'Ether slid 8% over the weekend.'),
 ('D', 'ETH', 'Ethereum is trading near $2,500.'),
 ('D', 'BTC', 'Bitcoin hovered around $62,000.'),
 ('D', 'BTC', 'BTC reclaimed $60K after the Fed decision.'),
 ('D', 'BTC', "Bitcoin's price fell sharply on the news."),
 ('D', 'SOL', "The token's 20% weekly decline erased its gains."),
 ('D', 'BTC', "Bitcoin's rally stalled near resistance."),
 ('D', 'BTC', 'Crypto prices tumbled amid risk-off sentiment.'),
 ('D', 'SOL', 'The coin is down 12% this week.'),
 ('D', 'BTC', 'Bitcoin broke above $70,000 for the first time since March.'),
 ('D', 'SOL', 'Solana surged after the ETF approval.'),
 ('D', 'BTC', 'Bitcoin set a new all-time high.'),
 ('D', 'XRP', 'XRP spiked on the court ruling.'),
 ('D', 'BTC', 'Bitcoin extended its losing streak to five days.'),
 ('D', 'BTC', 'BTC is up 40% year to date.'),
 ('D', 'ETH', 'Ethereum underperformed Bitcoin this month.'),
 ('E', 'CRWV', 'Shares closed at $145 on Friday.'),
 ('E', 'CRWV', 'The stock traded near $3,000.'),
 ('E', 'CRWV', 'Its valuation surged past $100 billion.'),
 ('E', 'CRWV', 'Its market value swelled to $70 billion.'),
 ('E', 'CRWV', 'The sell-off wiped $20 billion off its market value.'),
 ('E', 'CRWV', 'CoreWeave became a $100 billion company.'),
 ('E', 'SPY', 'SPY slipped 1% as yields rose.'),
 ('F', 'CRWV', 'A 2.2% pullback followed the debt news.'),
 ('F', 'CRWV', 'The stock extended its losing streak.'),
 ('F', 'CRWV', 'Extending its losing streak, the company still won a contract.'),
 ('F', 'CRWV', 'CoreWeave erased earlier gains after the filing.'),
 ('F', 'CRWV', 'CoreWeave pared losses after the upgrade.'),
 ('F', 'CRWV', 'Investors sold off the stock after the lockup expiry.'),
 ('F', 'CRWV', 'Selling pressure followed the convertible-note offering.'),
 ('F', 'CRWV', 'Gains of 40% year to date reflect AI enthusiasm.'),
 ('F', 'CRWV', 'The news sent shares higher.'),
 ('F', 'CRWV', 'The downgrade weighed on the stock.'),
 ('F', 'CRWV', 'The deal lifted shares to a record.'),
 ('F', 'CRWV', 'The debt raise pressured shares.'),
 ('F', 'CRWV', 'It posted its best day since March.'),
 ('F', 'CRWV', 'The drop marked its worst week since the IPO.'),
 ('F', 'CRWV', 'CoreWeave is now 40% below its June peak.'),
 ('F', 'CRWV', 'CoreWeave trades well above its $40 IPO price.'),
 ('F', 'CRWV', 'Investors bid up the stock after the deal.'),
 ('F', 'CRWV', 'The stock took a beating after the guidance cut.'),
 ('F', 'CRWV', 'CoreWeave lost half its value in two months.'),
 ('F', 'CRWV', 'A double-digit percentage drop followed the guidance.'),
 ('F', 'CRWV', 'The stock gave back its gains.'),
 ('F', 'CRWV', 'Short sellers pushed the stock lower.'),
 ('F', 'CRWV', 'Investors piled into the stock after the deal.'),
 ('F', 'CRWV', 'Traders dumped shares after the lockup.'),
 ('F', 'CRWV', 'The rally lifted CoreWeave to a $70 billion valuation.'),
 ('F', 'CRWV', 'Despite the rally, debt remains a concern.'),
 ('F', 'CRWV', 'A 10% correction followed the lockup expiry.'),
 ('F', 'CRWV', 'The stock remains below its IPO price.'),
]

# Written while building the detector: headlines, symbol-only terms, more verb / level
# shapes. (scope, text)
FLAG_PROBES = [
 # 2026-10-09 replay leaks, round 2 (peer groups, downward pressure):
 ('META', 'Social media stocks are rallying, with Meta holding flat amid broader market interest in AI.'),
 ('META', 'Social media stocks rallied, with Snap leading, while Meta held flat.'),
 ('CRWV', 'AI stocks, including CoreWeave, declined following a lower-than-expected revenue report from OpenAI.'),
 ('CRWV', "CoreWeave's stock is facing downward pressure from the debt load."),
 ('CRWV', 'The stock is under downward pressure.'),
 # 2026-10-09 replay leaks (PLUG / CRWV under the v8 prompt):
 ('CRWV', 'CoreWeave faces a challenging market environment with declining stock performance.'),
 ('CRWV', 'The stock is part of a broader downward trend affecting AI infrastructure companies.'),
 ('CRWV', 'A recent OpenAI revenue report caused AI stocks, including CoreWeave, to sink.'),
 # Stored v7 cards copied the old price line ("…in the latest regular session"); TestFlight
 # 2026-10-09 PLUG / META cards:
 ('CRWV', 'CRWV stock closed down 2.89% in the latest regular session.'),
 ('CRWV', 'CRWV closed down 2.89%.'),
 ('CRWV', 'The stock closed 2.9% lower.'),
 ('CRWV', 'CRWV stock is down 2.89% in the latest regular session.'),
 ('CRWV', 'Shares ended up 3% on Friday.'),
 ('META', 'Social media stocks, including Meta, showed a flat performance as the sector rallied.'),
 ('CRWV', 'The broader market sold off on Tuesday.'),
 # Review 2026-10-09 (dated move nouns, soft verbs, index changes, STOCK Act, GLD):
 ('CRWV', 'A 2.2% slip on Monday followed the filing.'),
 ('CRWV', 'After a 5% drop on Tuesday, analysts stayed bullish.'),
 ('CRWV', 'Despite the 9% rally on Friday, the backlog matters more.'),
 ('CRWV', "CoreWeave's 2.2% slip on Monday came after a downgrade."),
 ('CRWV', 'Strong demand was countered by recent price declines on Monday, Oct 5.'),
 ('CRWV', 'A 2.2% decline on Monday tempered the news.'),
 ('CRWV', "The stock's 2.2% drop on Monday weighed on sentiment."),
 ('CRWV', 'A 2.2% slip in early trading followed.'),
 ('CRWV', 'Selling pressure on Monday offset the news.'),
 ('CRWV', 'Shares steadied after the sell-off.'),
 ('CRWV', 'The stock has been a steady gainer this year.'),
 ('CRWV', 'The stock dropped from $50 to $40.'),
 ('CRWV', 'Shares dropped from their record high on Tuesday.'),
 ('CRWV', 'The stock lost its gains by the close.'),
 ('CRWV', 'The stock fell after the S&P 500 rebalance.'),
 ('CRWV', "The stock dropped from the S&P 500's top ten performers."),
 ('CRWV', 'The stock fell from the index high.'),
 ('NVDA', 'Nvidia stock fell 3% after a STOCK Act filing showed a lawmaker sold.'),
 ('NVDA', 'The stock lagged the S&P 500 after the STOCK Act filing.'),
 ('CRWV', 'Traders took profits.'),
 ('ORCL', 'Questions about OpenAI revenue have impacted Oracle, alongside Microsoft, causing stock to fall.'),
 ('ORCL', 'The guidance cut caused the stock to drop.'),
 ('ORCL', 'The downgrade led shares to slide.'),
 ('ORCL', 'The miss caused its shares to tumble.'),
 # The 2026-10-09 CRWV replays (real Flash-Lite output under the v8 prompt), after the
 # "stock experienced a slip" point was dropped.
 ('CRWV', "CoreWeave's AI cloud business shows strength amid mixed stock performance"),
 ('CRWV', 'Despite a booming neocloud business and positive analyst views, CoreWeave faces mixed market signals and stock performance.'),
 ('CRWV', 'Its share performance has lagged the backlog story.'),
 ('CRWV', 'The stock fell 5%.'),
 ('ORCL', 'Oracle stock is down 12% this year.'),
 ('ORCL', "Oracle's stock has rallied 80% this year."),
 ('CRWV', 'Shares jumped after hours.'),
 ('CRWV', 'The stock rose to a record $345.'),
 ('CRWV', 'Shares slipped 2.2% on Tuesday.'),
 ('ORCL', 'Oracle shares have pulled back.'),
 ('CRWV', 'The 5% drop followed the guidance cut.'),
 ('CRWV', 'Despite the 5% drop, analysts stayed bullish.'),
 ('BTC', 'Bitcoin is nearing $100,000.'),
 ('BTC', 'Bitcoin crossed $100K.'),
 ('BTC', 'Bitcoin traded at $61,500 on Monday.'),
 ('CRWV', 'The stock trades near its all-time high.'),
 ('CRWV', 'Shares are near a 52-week low.'),
 ('CRWV', 'CoreWeave stock has fallen 50% from its high.'),
 ('CRWV:sym', 'CRWV fell 5%.'),
 ('CRWV', 'CoreWeave Tumbles After Earnings'),
 ('COIN', 'Coinbase shares slid with bitcoin.'),
 ('BTC', 'Bitcoin slid with risk assets.'),
 ('ETH', 'Ether rallied alongside Bitcoin.'),
 ('XRP', 'XRP soared 20%.'),
 ('DOGE', 'Dogecoin plunged.'),
 ('SOL', "Solana's 15% weekly gain drew traders."),
 ('CRWV', "The stock's price fell 5%."),
 ('CRWV', 'Share price performance lagged peers.'),
 ('CRWV', 'Shares this year have doubled.'),
 ('BRK', 'Class A shares rose 3%.'),
 ('CRWV', "The firm's shares rose 3%."),
 ('ORCL', 'Shares of Oracle rose 5% after hours.'),
 ('NVDA', 'Its stake in CoreWeave is worth $3 billion after the shares rose.'),
 ('CRWV', 'A short squeeze lifted the stock.'),
 ('ORCL', 'Oracle jumped 8% after earnings.'),
 ('ORCL', 'Oracle jumped after earnings.'),
 ('ORCL', 'Oracle fell.'),
 ('ORCL:sym', 'ORCL slid 4% to $210.'),
 ('ORCL:sym', 'Oracle stock hit a 52-week high.'),
 ('BRK', 'BRK-B rose 1%.'),
 ('BTC', 'The news sent Bitcoin higher.'),
 ('CRWV', "CoreWeave's valuation surged past $100 billion."),
 ('CRWV', "CoreWeave's market value fell to $40 billion."),
 ('CRWV', 'The stock has been on a tear since the IPO.'),
 ('ETH', 'ETH price fell 5%.'),
 ('MSTR', 'Bitcoin prices fell 5%.'),
 ('CRWV', "It was the stock's worst session since April."),
 ('CRWV', 'Shares rallied 12% in a week.'),
 ('CRWV', 'CoreWeave rose 6% in early trading.'),
 ('ORCL', 'Oracle Soars On Cloud Backlog'),
 ('BTC', 'Bitcoin Holds Above $60,000'),
 ('CRWV', 'Why CoreWeave Stock Is Falling Today'),
 ('BTC', 'Bitcoin dropped under $60,000.'),
 ('CRWV', 'Options volume doubled as the stock rallied.'),
 ('ORCL', 'The stock rallied ahead of its next report in December.'),
 ('BTC', 'Bitcoin dipped below $60,000, its lowest since March.'),
 ('CRWV', 'Shares closed at $145, a record.'),
 ('ETH', 'Ethereum traded near $2,500, then slid.'),
 ('ORCL', 'Shares of Oracle have climbed 60% this year on AI optimism.'),
 ('ORCL', "Oracle's stock price has surged."),
 ('ORCL', "The stock's rally has been driven by AI."),
 ('ORCL', "Oracle's shares are up about 80% year to date."),
 ('CRWV', "CoreWeave's stock price has been volatile."),
 ('CRWV', 'The shares have been under pressure since the IPO.'),
 ('CRWV', 'The share price reaction was muted.'),
 ('ORCL', "The stock's reaction to earnings was muted."),
 ('CRWV', 'Investors sent the stock up 10%.'),
 ('ORCL', 'Oracle stock popped 10% on the news.'),
 ('CRWV', 'Its market cap has doubled since the IPO.'),
 ('CRWV', 'Investors saw shares fall 5% after the filing.'),
 ('CRWV:sym', 'CRWV  (+6%) led the AI names.'),
]

# Review round 3 (2026-10-09, adversarial). Rows carry the terms `price_terms` sends in
# production (lower-case names, two-word prefixes, sometimes a head word) — (terms, text).
_T_TGT = ("TGT", "target")
_T_V = ("V", "visa")
_T_GAP = ("GAP", "gap")
_T_SNAP = ("SNAP", "snap")
_T_OPEN = ("OPEN", "opendoor")
_T_DAL = ("DAL", "delta air lines", "delta air", "delta")
_T_HD = ("HD", "home depot")
_T_AAPL = ("AAPL", "apple")
_T_ORCL = ("ORCL", "oracle")
_T_META = ("META", "meta platforms", "meta")
_T_AMZN = ("AMZN", "amazon.com")
_T_A = ("A", "agilent")
_T_F = ("F", "ford motor", "ford")
_T_SOL = ("SOLUSD", "SOL", "Solana")
_T_DOGE = ("DOGEUSD", "DOGE", "Dogecoin")
_T_AMD = ("AMD", "advanced micro devices", "advanced micro")
_T_HOOD = ("HOOD", "robinhood markets", "robinhood")
_T_TSLA = ("TSLA", "tesla")
_T_WMT = ("WMT", "walmart")
_T_NVDA = ("NVDA", "nvidia")
_T_NFLX = ("NFLX", "netflix")
_T_MU = ("MU", "micron technology", "micron")
_T_CRWV = ("CRWV", "coreweave")
_T_PLTR = ("PLTR", "palantir")
_T_INTC = ("INTC", "intel")
_T_NKE = ("NKE", "nike")
_T_SPY = ("SPY", "spdr s&p 500 etf trust", "spdr s&p", "spdr")
_T_QQQ = ("QQQ", "invesco qqq trust series 1", "invesco qqq", "invesco")
_T_GLD = ("GLD", "spdr gold shares", "spdr gold", "spdr")
_T_BTC = ("BTCUSD", "BTC", "Bitcoin")
_T_ETH = ("ETHUSD", "ETH", "Ethereum", "Ether")
_T_XRP = ("XRPUSD", "XRP")
_T_KO = ("KO", "coca-cola")
_T_UNH = ("UNH", "unitedhealth")
_T_META2 = _T_META

HONEST_ROUND3 = [
 (_T_TGT, 'Target said stock availability rose to its highest level in three years.'),
 (_T_V, 'Visa topped $3 in quarterly earnings per share for the first time.'),
 (_T_V, 'Visa expects net revenue to post a low double-digit percentage gain next year.'),
 (_T_GAP, 'Comparable sales at Banana Republic posted a 1.5% slip versus last year.'),
 (_T_SNAP, "A 10% pullback by advertisers in North America hurt Snap's revenue."),
 (_T_OPEN, 'Opendoor pared losses, posting its first positive adjusted EBITDA in three years.'),
 (_T_OPEN, "Opendoor's model assumes a 5% correction, so it cut home acquisitions."),
 (_T_DAL, 'Delta had its worst week since 2024 for flight cancellations after storms hit Atlanta.'),
 (_T_HD, 'Home Depot expects comparable sales to post a low-single-digit percentage gain.'),
 (_T_HD, "Home Depot's Pro business outperformed the market, gaining share with contractors."),
 (_T_AAPL, "Apple gained while Huawei slipped in China's premium smartphone segment."),
 (_T_AAPL, "Apple's price stays at $799 for the base iPhone 17, despite tariffs."),
 (_T_AAPL, 'Apple reached $1.85 in quarterly earnings per share, a September-quarter record.'),
 (_T_ORCL, 'Oracle advanced on several fronts: OCI revenue rose 55% and RPO hit $455 billion.'),
 (_T_META, 'Threads logged its best week since launch for new sign-ups, Meta said.'),
 (_T_META, 'Daily Reels shares between friends rose 50%, Meta said.'),
 (_T_AMZN, 'Amazon said Trainium3 improves its price performance by 40% over Trainium2.'),
 (_T_AMZN, 'Amazon-backed Anthropic said its valuation rose to $183 billion in the funding round.'),
 (("ET", "energy transfer"), "Energy Transfer's fee-based earnings are little affected by price swings."),
 (_T_A, "A surge after the tariff pause lifted Agilent's instrument orders 12%."),
 (_T_A, "A slide as pharma customers paused purchases hurt Agilent's orders last year."),
 (("AI", "c3.ai"), "AI surged as a priority for federal agencies, lifting C3.ai's public-sector bookings."),
 (_T_F, "Ford's U.S. sales outperformed the market, rising 8% in the third quarter."),
 (_T_SOL, 'Solana surged as the top chain for memecoin launches, according to DefiLlama.'),
 (_T_DOGE, 'The cryptocurrency has gained traction with payment processors like PayPal.'),
 (_T_DOGE, "The token gained a listing on Robinhood's European platform."),
 (("CRCL", "circle internet", "circle"),
  "USDC's market capitalization rose to $73 billion, lifting Circle's reserve income."),
 (("MELI", "mercadolibre"),
  "The Argentine peso lost half its value, cutting MercadoLibre's reported revenue in dollars."),
 (("TM", "toyota motor", "toyota"),
  "The yen lost 10% of its value against the dollar, boosting Toyota's export profits."),
 (("DKNG", "draftkings"), "A winning streak for favored NFL teams hurt DraftKings' sportsbook hold."),
 (_T_AMD, "AMD's data-center unit extended its winning streak to 10 straight quarters of growth."),
 (_T_AMD, "AMD touts its price performance edge over Nvidia's Blackwell chips."),
 (("GETY", "getty images", "getty"), "Getty Images' stock photo revenue fell 5% as AI tools cut demand."),
 (("MSM", "msc industrial direct", "msc industrial"), "MSC's September ADS rose 4.2% year over year."),
 (("BK", "bank of new york mellon", "bank of"), 'ADR issuance fees rose 8% on new listings from India.'),
 (_T_HOOD, "Robinhood's equity volumes (stock, options) rose 25% in September."),
 (_T_HOOD, "The S&P 500's market cap threshold rose to $22.7 billion, a bar Robinhood now clears."),
 (("CBOE", "cboe global markets", "cboe global", "cboe"),
  'Cboe posted a jump in shares traded on its U.S. equities exchanges.'),
 (("BLK", "blackrock"), 'BlackRock said 85% of its active funds outperformed the benchmark over five years.'),
 (_T_TSLA, "Musk's new award vests in tranches as Tesla's market value rises from $2 trillion to "
           "$8.5 trillion."),
 (_T_TSLA, "The new CEO's pay package is tied to share price performance against peers."),
 (_T_WMT, 'Walmart jumped on the agentic-AI wave with a ChatGPT shopping deal.'),
 (_T_WMT, 'Retail sales jumped during the run-up ahead of the tariff deadline.'),
 (_T_NVDA, 'Jefferies sees the stock rising 30% over the next 12 months on Rubin demand.'),
 (_T_NFLX, "Netflix's price rose to $17.99 for the standard plan in the U.S."),
 (("HKXCY", "hong kong exchanges and clearing", "hong kong", "hong"),
  'Northbound Stock Connect turnover rose 40% to a record in September.'),
 (("FIVE", "five below", "five"), 'Tariffs hit stock availability at Five Below in the second quarter.'),
 (("GM", "general motors"), "Dealer stock fell to 52 days' supply, the lowest since 2023."),
 (_T_AAPL, 'Total shares fell 3% over the year as Apple bought back $90 billion of stock.'),
 (("RKT", "rocket companies", "rocket"), 'Mortgage applications posted a 20% rebound last week as rates eased.'),
 (_T_OPEN, 'Opendoor said selling pressure from homeowners has eased in Sun Belt markets.'),
 (_T_TSLA, 'Tesla Shares Robotaxi Update, Says Rides Doubled in October'),
 (_T_ORCL, 'Oracle Shares OCI Growth Plan — Revenue Jumps 77%'),
 (_T_MU, 'Steep price declines hurt NAND margins at Micron.'),
]

# Written while fixing round 3: the honest twin of each new shape. (terms, text)
HONEST_ROUND3_TWINS = [
 (_T_BTC, "Ethereum's gas price fell to 1 gwei."),
 (_T_AAPL, "Apple's iPhone 17 price stays at $799."),
 (_T_TSLA, 'Tesla pushes higher prices onto buyers.'),
 (_T_AAPL, 'Apple is higher than Samsung in customer satisfaction.'),
 (_T_ORCL, 'Oracle eclipsed its previous record for bookings.'),
 (_T_ORCL, 'Oracle set a record for cloud revenue.'),
 (_T_ORCL, 'Oracle hit a record in quarterly bookings.'),
 (_T_CRWV, 'At $40 a share, CoreWeave raised $1.5 billion in its IPO.'),
 (_T_CRWV, 'Shares were sold at $40 a share in the IPO.'),
 (_T_V, 'Visa reached $3 a share in adjusted earnings.'),
 (_T_SOL, 'Solana outpaced Ethereum in DEX volume this month.'),
 (_T_ETH, 'Ethereum lagged Solana in transactions for a third straight month.'),
 (("BLK", "blackrock"), 'Its active funds have outperformed the S&P 500 over five years.'),
 (_T_TSLA, 'Tesla had its best quarter since 2021 for deliveries.'),
 (_T_TSLA, 'Tesla had its best month since 2020 in China.'),
 (_T_NFLX, 'Netflix doubled in size over a decade.'),
 (_T_CRWV, 'The company rose to the top of the rankings.'),
 (_T_OPEN, 'Home prices entered correction territory in Austin.'),
 (_T_CRWV, 'Revenue recovered from a 10% decline last year.'),
 (_T_CRWV, 'Sales growth followed a 10% decline last year.'),
 (_T_INTC, 'Intel took profits from the Mobileye stake sale.'),
 (_T_CRWV, 'The 5% decline in bookings was driven by one customer.'),
 (_T_HOOD, 'Peers outperformed on revenue growth.'),
 (_T_INTC, 'Rival AMD gained 5% share in servers.'),
 (_T_CRWV, 'Rival Nebius rose 8% in revenue.'),
 (_T_TSLA, "Tesla's Model Y price fell to $39,990 in the U.S."),
 (_T_TSLA, 'Tesla cut the Model Y to $39,990.'),
 (_T_AAPL, "Apple's iPhone 17 starts at $799."),
 (("BKNG", "booking holdings", "booking"), 'Booking profits rose 10% on travel demand.'),
 (_T_WMT, "Walmart's winning streak at the box office continued."),
 (_T_CRWV, 'The run-up to the election delayed contracts.'),
 (_T_CRWV, 'Trading in the stock was halted pending news.'),
 (_T_HOOD, 'Robinhood shares data with regulators.'),
 (_T_SPY, 'The fund lost 10% of its assets to redemptions.'),
 (_T_GLD, 'Gold holdings in the trust rose 12 tonnes.'),
 (_T_AAPL, 'Apple eyes $799 iPhone for India.'),
 (_T_TSLA, 'Tesla eyes $25,000 car for 2027.'),
 (_T_ETH, 'Ethereum fees dropped to $0.01 per transfer.'),
 (_T_BTC, 'Bitcoin miners sold 5,000 BTC at about $110,000 each.'),
 (_T_ORCL, 'Oracle Shares Extend Partnership With OpenAI'),
 (_T_CRWV, 'The deal is a 30% premium to the closing price.'),
 # the self-review after round 3: each one hit a rule added for round 3 until narrowed
 (_T_AAPL, 'Apple returned 20% more cash to shareholders than last year.'),
 (_T_WMT, 'Walmart added 5% to its workforce before the holidays.'),
 (_T_DAL, 'Delta hit a record on Sunday, carrying 3 million passengers.'),
 (_T_DAL, 'Delta reached a record as travelers returned.'),
 (_T_CRWV, 'Shares were set at $40 in the IPO.'),
 (_T_CRWV, 'CoreWeave is up 200% in revenue this year.'),
 (_T_CRWV, 'CoreWeave, up 200% in revenue, raised guidance.'),
 (_T_BTC, 'Bitcoin had its best quarter for ETF inflows since launch.'),
 (_T_CRWV, 'Shares of revenue from Microsoft fell to 62%.'),
 (_T_ORCL, 'Oracle Shares Extend Partnership — Revenue Jumps 77%'),
 (("NEM", "newmont"), "Newmont's earnings outperformed gold prices this quarter."),
 (_T_CRWV, 'Shares are worth $40 under the deal terms.'),
 (("BLK", "blackrock"), 'The fund returned 12% last year.'),
 (_T_CRWV, 'Shares had their best quarter of buybacks.'),
 (_T_BTC, 'Miners sold 500 BTC at about $110,000.'),
]

# The price-claim twins of those narrowings (must stay flagged). (terms, text)
FLAG_ROUND3_PROBES = [
 # GLD's own name is never the generic "shares" subject — its name rows catch it
 (_T_GLD, 'SPDR Gold Shares hit an all-time high.'),
 (_T_CRWV, 'In 2025 CoreWeave rose 300%.'),
 (_T_ORCL, 'Oracle Shares Extend Rally'),
 (_T_ORCL, 'Oracle shares hit a record on Tuesday.'),
 (("NEM", "newmont"), 'Newmont has outperformed gold this year.'),
 (_T_CRWV, 'CoreWeave is up 40% this year.'),
 (_T_BTC, 'Bitcoin had its best quarter since 2021.'),
 (_T_SPY, 'SPY hit a record on Tuesday.'),
 (_T_CRWV, 'Shares are worth $40 after the rally.'),
]

# Round-3 must-flag rows: the adversary's misses this detector now catches. (terms, text)
FLAG_ROUND3 = [
 # moved from PROMPT_ONLY_ROUND3 on 2026-10-09: the group_stocks_move row (stockpile senses
 # excluded positionally) catches it — a peer/sector move is banned on a ticker card
 (_T_CRWV, "AI stocks rallied after Nvidia's report."),
 (_T_CRWV, 'CoreWeave skyrocketed 30% after the Meta contract.'),
 (_T_CRWV, 'Shares skyrocketed 30% after the Meta contract.'),
 (_T_OPEN, 'Opendoor rockets 15% as retail traders pile in.'),
 (_T_SNAP, 'Snap leapt 12% in after-hours trading.'),
 (_T_SNAP, 'Shares leaped 12% after the earnings beat.'),
 (_T_PLTR, 'Palantir zoomed to a record after the Army contract.'),
 (_T_HOOD, 'Robinhood vaulted 9% to an all-time high.'),
 (_T_INTC, 'Intel shares collapsed 26% after the dividend cut.'),
 (("BA", "boeing"), 'Boeing dove 8% after the FAA grounding.'),
 (_T_NKE, 'Nike stumbled 6% on weak China sales.'),
 (_T_GAP, 'Gap shares firmed 2% after the upgrade.'),
 (_T_TGT, 'Target Stock Weakens After Guidance Cut'),
 (_T_ORCL, 'Oracle closed at a record on Tuesday.'),
 (_T_ORCL, 'Shares closed at a record after the RPO disclosure.'),
 (_T_SPY, 'The ETF hit an all-time high on Friday.'),
 (_T_QQQ, 'QQQ notched a record close on Thursday.'),
 (_T_GLD, 'The fund fell 3% as gold retreated from its record.'),
 (_T_NVDA, 'Nvidia rose 3% in Friday trading.'),
 (_T_BTC, 'Bitcoin fell 3% in Asian trading.'),
 (_T_AAPL, "Apple rose 4% in Tuesday's session."),
 (_T_BTC, 'The price of Bitcoin fell 4% overnight.'),
 (_T_ETH, 'The price of ETH slid below $3,000.'),
 (_T_SOL, 'The SOL price surged 10% on ETF approval hopes.'),
 (_T_XRP, 'The XRP price jumped 12% after the ruling.'),
 (_T_DOGE, 'The Dogecoin price slipped to 18 cents.'),
 (_T_BTC, 'BTC/USD slipped below $110,000.'),
 (_T_SOL, 'Its price fell 8% after the token unlock.'),
 (_T_ETH, 'Price action was choppy after the Fed decision.'),
 (_T_SOL, "Solana's price action turned bullish above $200."),
 (_T_BTC, "Bitcoin's surge to $120,000 drew retail buyers."),
 (_T_BTC, "Bitcoin's drop below $100,000 triggered liquidations."),
 (_T_ETH, "Ether's climb above $4,000 lifted DeFi activity."),
 (_T_BTC, "Bitcoin's breakout above $120,000 came on ETF inflows."),
 (_T_BTC, 'Bitcoin slipped under $100,000 for the first time since June.'),
 (_T_BTC, 'Bitcoin Slips Below $100K as ETF Outflows Mount'),
 (_T_ETH, 'Ether Tumbles Below $3,000'),
 (_T_BTC, 'Bitcoin plunges below $100K after the tariff threat.'),
 (_T_ETH, 'Ether plummets to $2,500 as liquidations mount.'),
 (_T_XRP, 'XRP soars to $3 after the SEC settlement.'),
 (_T_BTC, 'Bitcoin spikes to $118,000 on the CPI print.'),
 (_T_ETH, 'Ether retreats to $4,000 after a record run.'),
 (_T_BTC, 'Bitcoin rebounds to $110K on dip buying.'),
 (_T_BTC, 'Bitcoin Edges Higher as ETF Flows Return'),
 (_T_ETH, 'Ether Moves Higher After Fusaka Upgrade'),
 (_T_SOL, 'Solana Trades Lower Despite ETF Launch'),
 (_T_XRP, 'XRP Drifts Lower Into the Weekend'),
 (_T_BTC, 'Bitcoin pushes higher toward $115,000.'),
 (_T_BTC, 'Bitcoin steadies near $110,000 after a volatile week.'),
 (_T_BTC, 'Bitcoin consolidates near $110K.'),
 (_T_BTC, 'Bitcoin stalls at $112,000 resistance.'),
 (_T_BTC, 'Bitcoin struggles below $110,000.'),
 (_T_BTC, 'Bitcoin Eyes $120K as Inflows Return'),
 (_T_BTC, 'Bitcoin flirts with $120,000.'),
 (_T_BTC, 'Bitcoin defends $100K support.'),
 (_T_BTC, 'Bitcoin surpassed $120,000 for the first time.'),
 (_T_BTC, 'Bitcoin breached $100,000 on Tuesday.'),
 (_T_BTC, 'Bitcoin smashed through $120K.'),
 (_T_BTC, 'Bitcoin blew past $125,000 to a new record.'),
 (_T_BTC, 'Bitcoin eclipsed its previous record of $124,000.'),
 (_T_SOL, 'Solana zoomed past $250.'),
 (_T_DOGE, 'Dogecoin jumps above 30 cents.'),
 (_T_DOGE, 'DOGE trades at 25 cents after the ETF filing.'),
 (("ONEUSD", "ONE", "Harmony"), 'ONE trades at about 1 cent, down from its peak.'),
 (_T_BTC, 'With Bitcoin above $100,000, miner margins have widened.'),
 (_T_BTC, 'Bitcoin, now at $110,000, is up 18% this year.'),
 (_T_BTC, 'Bitcoin around $110K as ETF flows slow'),
 (_T_BTC, 'Bitcoin is now worth about $110,000.'),
 (_T_ETH, 'ETH is priced at about $4,000.'),
 (_T_XRP, 'XRP last traded at $2.80.'),
 (_T_BTC, 'Bitcoin is at its highest since July.'),
 (_T_XRP, 'XRP hit its highest level since 2018.'),
 (_T_ETH, 'Ether touched its lowest level in a year.'),
 (_T_BTC, 'Bitcoin fell below its 200-day moving average.'),
 (_T_BTC, 'Bitcoin broke above key resistance at $115,000.'),
 (_T_BTC, 'Bitcoin is testing support near $100K.'),
 (_T_ETH, 'Ether faces resistance at $4,500.'),
 (_T_BTC, 'Bitcoin remains well below its October peak.'),
 (_T_DOGE, 'DOGE added 12% in a week.'),
 (_T_BTC, 'Bitcoin added 3% overnight.'),
 (_T_BTC, 'Bitcoin topped $2 trillion in market value.'),
 (_T_NVDA, 'Nvidia hit $5 trillion in market value on Wednesday.'),
 (_T_NVDA, 'Nvidia briefly topped a $5 trillion valuation.'),
 (_T_CRWV, 'Year to date, the stock has returned 45%.'),
 (_T_PLTR, 'Shares have returned 120% over the past year.'),
 (_T_ORCL, 'At $300, the stock trades at 45 times earnings.'),
 (_T_KO, 'With shares at $70, the dividend yields 2.9%.'),
 (_T_NVDA, "The stock's 52-week range is $86 to $212."),
 (_T_AAPL, "The stock's closing price was $255.20."),
 (_T_META, 'Meta finished the session at $780.'),
 (_T_META, 'Shares ended the day at $780.10.'),
 (_T_TSLA, 'Shares peaked at $488 in December.'),
 (_T_NKE, 'The stock bottomed at $52 in April.'),
 (_T_TSLA, 'The stock has lagged the S&P 500 this year.'),
 (_T_AAPL, 'Apple shares have trailed peers in 2025.'),
 (_T_PLTR, 'Palantir has outpaced the Nasdaq this year.'),
 (_T_SNAP, 'The stock gapped higher at the open.'),
 (_T_HOOD, 'Shares ripped higher after the S&P 500 inclusion.'),
 (_T_UNH, 'The stock was hammered after the guidance cut.'),
 (_T_UNH, 'Shares got crushed on the guidance withdrawal.'),
 (_T_NKE, 'Shares were punished after the China miss.'),
 (_T_CRWV, 'Shares were cut in half from their June high.'),
 (_T_OPEN, 'Shares sit near multi-year lows.'),
 (_T_INTC, 'Shares languish near decade lows.'),
 (_T_KO, 'Shares have traded sideways for months.'),
 (_T_CRWV, 'The stock has been range-bound since July.'),
 (_T_CRWV, "CoreWeave was the Nasdaq 100's best performer in September."),
 (("FOUR", "shift4 payments", "shift4"), 'Shift4 was among the worst performers in the S&P MidCap 400.'),
 (_T_OPEN, 'Opendoor led gainers on the Nasdaq on Monday.'),
 (_T_SNAP, 'Snap was among the biggest decliners in the S&P 500.'),
 (("TM", "toyota motor", "toyota"), 'Toyota shares closed at ¥2,800 in Tokyo.'),
 (("SONY", "sony"), 'Sony closed at ¥3,500, a record.'),
 (("BABA", "alibaba"), "Alibaba's Hong Kong shares closed at HK$150."),
 (("BHP", "bhp"), 'Shares closed at A$45 in Sydney.'),
 (("BABA", "alibaba"), 'Shares closed at US$120 in New York.'),
 (("SHOP", "shopify"), 'Shopify closed at C$200 in Toronto.'),
 (("RELI", "reliance industries", "reliance"), 'Reliance closed at ₹1,400 in Mumbai.'),
 (_T_NVDA, 'Rival Nebius rose 8%.'),
 (_T_SPY, 'The fund closed at a record high.'),
 (_T_GLD, 'GLD hit a record as bullion topped $4,000.'),
 (_T_TSLA, 'Tesla Rockets Higher on Robotaxi Hopes'),
 (_T_ORCL, 'Oracle Skyrockets After Record Backlog'),
 (_T_NFLX, 'Netflix shed $40 billion in market value after the earnings miss.'),
 (_T_NFLX, 'Netflix lost $40 billion in market value.'),
 (_T_AAPL, 'Apple added $200 billion in market value on Friday.'),
 (_T_ETH, 'Ethereum overtook Mastercard in market value.'),
 (_T_AMZN, 'Amazon fell 3% after AWS growth missed.'),
 (("LOW", "lowe's companies", "lowe's"), 'Lowe’s fell 3% on the guidance.'),
 (("MCD", "mcdonald's"), 'McDonald’s slipped 2% after the traffic miss.'),
 (_T_BTC, "Bitcoin's up 5% since Monday."),
 (_T_CRWV, 'The stock trades about 20% below its 50-day moving average.'),
 (_T_CRWV, 'The stock trades 20% below where it began the year.'),
 (_T_CRWV, 'Shares recouped their losses after the Nvidia stake news.'),
 (_T_SOL, "Solana's price surge drew retail traders."),
 (_T_XRP, "XRP's price spike faded within hours."),
 (_T_SPY, 'The fund gained 1.8% last week.'),
 (_T_QQQ, 'The ETF slid 2.4% on Friday.'),
 (_T_BTC, 'Prices for Bitcoin fell sharply overnight.'),
 (_T_CRWV, 'A sharp move lower followed the downgrade.'),
 (_T_CRWV, "Profit-taking set in after the stock's run."),
 (_T_CRWV, 'Investors took profits after the big run.'),
 (_T_CRWV, 'The stock has been a big winner this year.'),
 (_T_CRWV, "CoreWeave has been one of the year's best-performing stocks."),
 (_T_BTC, 'Bitcoin is having its best month since 2020.'),
 (_T_CRWV, 'Shares are on pace for their best month since the IPO.'),
 (_T_CRWV, 'Shares posted their worst month since March.'),
 (_T_BTC, 'Bitcoin posted its worst quarter since 2022.'),
 (_T_CRWV, 'Shares are now in bear-market territory.'),
 (_T_CRWV, 'The stock entered correction territory.'),
 (_T_CRWV, 'Shares are up triple digits since the IPO.'),
 (_T_CRWV, 'Shares are up more than threefold since March.'),
 (_T_HOOD, 'Shares are now worth 10 times their 2023 low.'),
 (_T_BTC, 'One Bitcoin now costs more than $110,000.'),
 (_T_BTC, 'Bitcoin outpaced Ether this month.'),
 (_T_ETH, 'Ether lagged Bitcoin for a third straight month.'),
 (_T_ETH, 'The ETH/BTC ratio fell to a five-year low.'),
 (_T_BTC, 'Bitcoin volatility spiked to a three-month high.'),
 (_T_DOGE, 'Dogecoin doubled in a week.'),
 (_T_CRWV, 'At about $130 a share, CoreWeave is valued at $65 billion.'),
 (("MSTR", "strategy"), "Strategy's shares, at $350, trade at a premium to its bitcoin."),
 (_T_CRWV, 'The last trade was $128.'),
 (_T_CRWV, "CoreWeave's $130 share price values it at $65 billion."),
 (_T_ORCL, 'The stock closed at $45.20 per share on Friday.'),
 (_T_ORCL, 'Oracle closed at $345 a share, a record.'),
 (_T_ORCL, 'Shares hovered near $12 a share ahead of the vote.'),
 (_T_CRWV, 'CoreWeave traded at $130 a share.'),
]

# Owner decisions 2026-10-09 ALLOW these, although a reviewer listed them as must-flag.
RECLASSIFIED_ALLOWED = [
    ("BTC", "Bitcoin's dominance rose to 58%.",
     "dominance is a share of the crypto market, not a price; its honest twin is in round 1"),
    ("CRWV", "The stock now trades at 30 times forward earnings.", "a valuation multiple"),
    ("CRWV", "CoreWeave carries a market cap of $50 billion.", "market cap as a size"),
]

# Real price talk left to the prompt's PRICE rule. Asserted NOT flagged: catching any of
# these is a deliberate widening, to be weighed against its honest twin.
PROMPT_ONLY = [
    ("SPY", "The fund's NAV fell 1.2%.",
     "a BDC's or closed-end fund's NAV per share is a reported fundamental"),
    ("CRWV", "Volatile trading followed the lockup expiry.",
     "'volatile trading' drives a broker's revenue: honest on a COIN or HOOD card"),
    ("CRWV", "CoreWeave has been on a tear since its IPO.",
     "with the company as subject 'on a tear / on a roll' is also business momentum"),
    ("CRWV", "Momentum faded after the deal.", "bare 'momentum' is also bookings momentum"),
    ("CRWV", "Shares, after a weak open, rose 5%.",
     "a gap never crosses 'after' (it is how 'shares as revenue rose' stays honest)"),
    ("CRWV", "Shares of Oracle, Nvidia and AMD rose.", "a gap never crosses 'and'"),
    ("CRWV", "Investors fled the stock.", "open-ended trader verbs stay with the prompt"),
    ("CRWV", "The AI rally has fueled data-center spending.",
     "a sector rally; only timing adjectives make 'the rally' a price reference"),
    ("CRM", "A sell-off in software stocks hurt sentiment.",
     "'in <plural stocks>' reads as about something else, like 'a sell-off in bonds'"),
    ("CRWV", "The S&P 500 fell 1.2%.",
     "index moves: 'The broader stock market fell, but Oracle's backlog rose' is honest"),
]

# Round-3 misses left to the prompt. (terms, text, why)
PROMPT_ONLY_ROUND3 = [
    (_T_CRWV, "Its market value now exceeds $60 billion.",
     "a SIZE stated with a crossing verb; size is allowed, and 'exceeds' has no time"),
    (_T_BTC, "Bitcoin's market cap now exceeds that of Amazon.",
     "a size comparison, not a move"),
    (("GOOGL", "alphabet"), "Google rose 4% on the Gemini launch.",
     "price_terms sends no 'google' for GOOGL; the detector knows only the names it is given"),
    (("TSM", "taiwan semiconductor manufacturing", "taiwan semiconductor"), "TSMC slid 3% in Taipei.",
     "price_terms sends no 'TSMC'"),
    (("DIS", "walt disney"), "Disney fell 5% after the earnings report.",
     "price_terms sends no 'disney' (only 'walt disney')"),
    (("SMCI", "super micro computer", "super micro"), "Supermicro plunged 20% on the delay.",
     "price_terms sends no one-word 'supermicro'"),
    (("ON", "on semiconductor"), "Onsemi dropped 7% on weak guidance.",
     "price_terms sends no 'onsemi'; an all-lower-case brand can only match capitalised"),
]

# Review round 4 (2026-10-09, adversarial). (terms, text) as the reviewer sent them.
# The 62 lines round 4 found FLAGGED although honest: Form 4 values ("sold shares worth
# $850,000"), share pools and stock-on-hand, schedule idioms ("Apple Moves Up iPhone Fold
# Launch"), rankings ("moved higher in the J.D. Power rankings"), a return ON EQUITY, an
# event's best day, product price cuts, metric moves on the day, a chain's fee, the halving,
# an airdrop, private rounds and a pay design.
HONEST_ROUND4 = [
 (('CRWV', 'coreweave'), "CoreWeave's chief strategy officer sold shares worth about $850,000 last week."),
 (('NVDA', 'nvidia'), 'A director bought shares worth $420,000 in the open market, a filing showed.'),
 (('PLTR', 'palantir'), 'The CFO sold stock worth $640,000 under a trading plan.'),
 (('CRWV', 'coreweave'), 'Shares eligible for sale jumped to 1.2 billion when the lockup expired.'),
 (('CRWV', 'coreweave'), 'Free-float shares rose to 40% of the total after the lockup expired.'),
 (('PLTR', 'palantir'), 'Shares set aside for employee awards rose to 60 million.'),
 (('CRWV', 'coreweave'), 'Shares on loan rose to 30 million as short sellers built positions.'),
 (('PLTR', 'palantir'), 'Stock pay at Palantir rose 18% to $690 million.'),
 (('PLTR', 'palantir'), 'Stock dilution rose to 3% last year as Palantir paid staff in equity.'),
 (('TGT', 'target'), 'Target said stock on hand fell 3% from a year earlier.'),
 (('HAS', 'hasbro'), 'Hasbro cited a decline in stock at retailers ahead of the holidays.'),
 (('NVDA', 'nvidia'), 'Nvidia flagged a drop in GPU stock at distributors ahead of the Blackwell ramp.'),
 (('TSLA', 'tesla'), 'Tesla reported a rise in Model Y stock at U.S. delivery centers.'),
 (('F', 'ford motor', 'ford'), "Stock at Ford dealers rose to 100 days' supply in September."),
 (('AAPL', 'apple'), 'Apple Moves Up iPhone Fold Launch to 2026'),
 (('TSLA', 'tesla'), 'Tesla moved up its robotaxi expansion to five new cities.'),
 (('DAL', 'delta air lines', 'delta air', 'delta'), 'Delta edged up its full-year earnings outlook.'),
 (('AMZN', 'amazon.com'), 'Amazon Moves Down Market With a Cheaper Echo Show'),
 (('DAL', 'delta air lines', 'delta air', 'delta'), 'Delta moved higher in the J.D. Power customer satisfaction rankings.'),
 (('NFLX', 'netflix'), 'Netflix pushed higher subscription prices onto ad-free members.'),
 (('V', 'visa'), 'Visa is pushing higher interchange fees on premium cards, merchants say.'),
 (('TSLA', 'tesla'), "Weak reliability scores drove Tesla lower in Consumer Reports' rankings."),
 (('AAPL', 'apple'), "Strong iPhone 17 sales pushed Apple higher in China's smartphone rankings."),
 (('AAPL', 'apple'), "Apple gained on Huawei in China's premium smartphone segment."),
 (('F', 'ford motor', 'ford'), 'Ford gained on Tesla in U.S. electric-vehicle sales last quarter.'),
 (('HAS', 'hasbro'), 'Hasbro rebounded after a weak 2024, posting record Magic: The Gathering sales.'),
 (('SOLUSD', 'SOL', 'Solana'), 'Solana gained 30% more active addresses in September.'),
 (('MSTR', 'strategy'), 'Strategy Adds 2% to Bitcoin Holdings With $1 Billion Purchase'),
 (('PGR', 'progressive'), 'Progressive returned 34% on equity over the past year.'),
 (('SNAP', 'snap'), 'Snap pared losses as ad revenue grew 10%.'),
 (('F', 'ford motor', 'ford'), "Ford's Model e unit extended losses as battery costs rose."),
 (('AMZN', 'amazon.com'), "Prime Day was Amazon's best day since it began in 2015."),
 (('TGT', 'target'), "Black Friday was Target's best day in five years."),
 (('LYV', 'live nation entertainment', 'live nation'), 'Ticketmaster had its best week since 2019 as stadium tours went on sale.'),
 (('MU', 'micron technology', 'micron'), "Steep price declines hurt Micron's NAND margins."),
 (('DOW', 'dow'), "Ongoing price weakness, especially in Asia, hurt Dow's results."),
 (('DOW', 'dow'), 'Margins were affected by price weakness, especially in Asia.'),
 (('TSLA', 'tesla'), 'Recent price drops helped revive demand for the Model Y.'),
 (('F', 'ford motor', 'ford'), "Ford's move to $30,000 EVs depends on cheaper LFP batteries."),
 (('TGT', 'target'), 'Target set a $35 threshold for free same-day delivery.'),
 (('AMZN', 'amazon.com'), 'Amazon set a $25 free-shipping threshold for non-Prime members.'),
 (('CAT', 'caterpillar'), 'Copper climbed 3% in London trading on supply worries.'),
 (('ET', 'energy transfer'), 'Natural gas storage rose 3% on the week, the EIA said.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Spot Bitcoin ETF volume rose 30% on the day, Bloomberg data showed.'),
 (('SNAP', 'snap'), 'App downloads jumped 50% on the news, according to Sensor Tower.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'Spot Bitcoin ETFs outperformed Ether ETFs in net inflows this week.'),
 (('SOLUSD', 'SOL', 'Solana'), "Solana's DEX volume outperformed Ethereum's for a third straight month."),
 (('COIN', 'coinbase global', 'coinbase'), "Coinbase's spot volume underperformed the crypto market as a whole."),
 (('SOLUSD', 'SOL', 'Solana'), 'Solana outpaced Ethereum again, settling three times as many transactions.'),
 (('XRPUSD', 'XRP'), 'XRP lagged Bitcoin, with ETF inflows of only $100 million.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'Using Ethereum now costs about $0.02 per swap after the Fusaka upgrade.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'USDC market cap rose to $75 billion, much of it on Ethereum.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), "Since Bitcoin halved last year, miners' revenue per block fell by half."),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin halved after block 840,000, cutting the reward to 3.125 BTC.'),
 (('ONEUSD', 'ONE', 'Harmony'), 'Days after the token dropped, 40,000 wallets had claimed it.'),
 (('TSLA', 'tesla'), 'The automaker hit an all-time high with 497,099 deliveries in the third quarter.'),
 (('AMZN', 'amazon.com'), 'Anthropic, which Amazon backs, said its valuation tripled to $183 billion after the new investment.'),
 (('ORCL', 'oracle'), 'OpenAI said its valuation reached $500 billion after a secondary share sale.'),
 (('SNAP', 'snap'), "Snap's recent rebound with small advertisers helped revenue."),
 (('HAS', 'hasbro'), "Hasbro's recent rebound came from Magic: The Gathering and Monopoly Go."),
 (('DKNG', 'draftkings'), "A winning streak on NFL favorites hurt DraftKings' hold rate in September."),
 (('TSLA', 'tesla'), 'The new award vests only if stock price performance beats the S&P 500 over three years.'),
]

# Written while fixing round 4: the honest twin of each new rule. (terms, text)
HONEST_ROUND4_TWINS = [
 (('WMT', 'walmart'), 'Visits rose 5% on the week, Placer.ai said.'),
 (('PGR', 'progressive'), 'Claims rose 3% on the week.'),
 (('XOM', 'exxon mobil'), 'Brent rose 3% on the day.'),
 (('AAPL', 'apple'), 'Apple fetched $799 for the base model.'),
 (('F', 'ford motor', 'ford'), 'Ford hit a record high of $65,000 in average transaction prices.'),
 (('CVX', 'chevron'), 'Each share of Hess will be converted into 1.025 Chevron shares.'),
 (('CRWV', 'coreweave'), 'Each share of preferred stock is worth $25 at liquidation.'),
 (('CRWV', 'coreweave'), 'Shares were sold at a 5% discount from the offering price.'),
 (('CRWV', 'coreweave'), 'The follow-on priced at a 3% discount from the last offering price.'),
 (('SOLUSD', 'SOL', 'Solana'), 'Solana gained users while Ethereum lost them.'),
 (('SOLUSD', 'SOL', 'Solana'), 'Solana gained while Ethereum lagged in developer activity.'),
 (('TSLA', 'tesla'), 'Tesla moved up to second place in European EV sales.'),
 (('DAL', 'delta air lines', 'delta'), 'Delta moved up two spots in the rankings.'),
 (('NVDA', 'nvidia'), 'Nvidia edged up its revenue forecast.'),
 (('NVDA', 'nvidia'), "The surge in Nvidia demand lifted TSMC's revenue."),
 (('TSLA', 'tesla'), 'A decline in Tesla deliveries weighed on margins.'),
 (('SOLUSD', 'SOL', 'Solana'), 'The Solana token gained a listing on Coinbase.'),
 (('AAPL', 'apple'), 'The Apple stock buyback rose 10% last year.'),
 (('F', 'ford motor', 'ford'), 'Ford is in record territory for hybrid sales.'),
 (('MSTR', 'strategy'), 'Strategy dumped 15% of its software staff.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin hashrate was flat over the weekend.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin dominance was flat this week.'),
 (('ORCL', 'oracle'), "Oracle's value proposition rose with OCI."),
 (('NFLX', 'netflix'), "Netflix's value tier gained 5 million members."),
 (('INTC', 'intel'), "Intel's slide deck showed a $50 billion foundry pipeline."),
 (('SNAP', 'snap'), "Snap's sharp rebound in ad revenue lifted margins."),
 (('HAS', 'hasbro'), 'Hasbro rebounded from a weak 2024 with record Magic sales.'),
 (('AAPL', 'apple'), 'Apple gained on Samsung among premium buyers.'),
 (('F', 'ford motor', 'ford'), 'Ford gained on its rivals in pickup sales.'),
 (('CRWV', 'coreweave'), 'Revenue cooled after a strong run of quarters.'),
 (('CRWV', 'coreweave'), 'Stock comp cooled off as hiring slowed.'),
 (('CRWV', 'coreweave'), 'Stock pickers posted a 20% return last year.'),
 (('PGR', 'progressive'), 'Progressive delivered a 34% return on equity.'),
 (('MSTR', 'strategy'), 'Strategy added 2% to its bitcoin holdings.'),
 (('SOLUSD', 'SOL', 'Solana'), 'Solana gained 30% more developers in 2025.'),
 (('CRWV', 'coreweave'), 'Free-float shares rose to 45% of the company.'),
 (('CRWV', 'coreweave'), 'Shares eligible for sale rose to 900 million.'),
 (('TGT', 'target'), 'Stock on hand rose 4% at Target.'),
 (('WMT', 'walmart'), 'Stock at Walmart stores rose ahead of the holidays.'),
 (('TSLA', 'tesla'), "The Model Y's price fell to $39,990."),
 (('TSLA', 'tesla'), 'Recent price cuts boosted Model Y deliveries.'),
 (('MU', 'micron technology', 'micron'), 'Recent price declines helped lift unit volumes.'),
 (('ONEUSD', 'ONE', 'Harmony'), 'The token dropped to holders who staked before June.'),
 (('ONEUSD', 'ONE', 'Harmony'), 'After the airdrop, the token dropped into 40,000 wallets.'),
 (('AMZN', 'amazon.com'), 'Its valuation tripled to $60 billion in the latest tender.'),
 (('AMZN', 'amazon.com'), "Prime Day was the biggest one-day sales event in Amazon's history."),
 (('SNAP', 'snap'), 'It had its best week since launch for downloads.'),
 (('DKNG', 'draftkings'), "A winning streak on Sundays lifted DraftKings' handle."),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), "Ethereum's gas costs fell to $0.01 per transfer."),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Sending Bitcoin costs about $1 per transaction.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin halved its issuance in April 2024.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'The Ether ETFs outperformed the Bitcoin ETFs in net inflows.'),
 (('COIN', 'coinbase global', 'coinbase'), "Coinbase's derivatives volume outperformed the crypto market."),
 (('XRPUSD', 'XRP'), 'XRP outpaced Ethereum, with 1.5 million daily transactions.'),
 (('TSLA', 'tesla'), 'Tesla had its best day in five years for deliveries in China.'),
 (('DAL', 'delta air lines', 'delta'), 'Delta posted a record high load factor of 88%.'),
 (('CRWV', 'coreweave'), 'Its peak of $5 billion in quarterly revenue came in 2022.'),
 (('XOM', 'exxon mobil'), 'Gas prices hit their high of $5.02 a gallon in June.'),
 (('Z', 'zillow'), 'Home prices are near their record high of $422,000.'),
 (('WY', 'weyerhaeuser'), 'Lumber traded near its 2021 peak of $1,700.'),
 (('NVDA', 'nvidia'), 'The startup became worth $10 billion after its Series C round.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin is down today after a network upgrade.'),
 (('SOLUSD', 'SOL', 'Solana'), 'Solana is down today after a validator outage.'),
 (('CRWV', 'coreweave'), 'CoreWeave is down 200 employees after layoffs.'),
 (('NFLX', 'netflix'), 'Netflix pushed higher prices onto members.'),
 (('AAPL', 'apple'), 'Apple moved lower in the J.D. Power rankings.'),
 (('RKT', 'rocket companies'), 'Rising rates sent Rocket Companies higher in loan-servicing rankings.'),
]

# Round-4 misses this detector now catches. (terms, text)
FLAG_ROUND4 = [
 (('OPEN', 'opendoor'), 'OPEN rose 500% in two months.'),
 (('ORCL', 'oracle'), 'Oracle posted its biggest one-day gain since 1992.'),
 (('ORCL', 'oracle'), 'ORCL pulled back 6% after the OpenAI report.'),
 (('TRUMPUSD', 'TRUMP', 'Official Trump'), 'Official Trump slumped below $8.'),
 (('TRUMPUSD', 'TRUMP', 'Official Trump'), 'The TRUMP memecoin is down 80% from its January peak.'),
 (('TRUMPUSD', 'TRUMP', 'Official Trump'), 'The TRUMP token is down 80%.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), "Bitcoin's price has doubled in a year."),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin traded flat over the weekend.'),
 (('XRPUSD', 'XRP'), 'XRP is changing hands near $3.'),
 (('TSLA', 'tesla'), 'Tesla stock reversed course after an early gain.'),
 (('CRWV', 'coreweave'), 'CoreWeave shares changed direction after the Core Scientific vote.'),
 (('CRWV', 'coreweave'), 'CoreWeave stock went from $40 to $187 in three months.'),
 (('CRWV', 'coreweave'), 'Its stock is now well below its June peak of $187.'),
 (('CRWV', 'coreweave'), 'CoreWeave shares cooled off after a strong run.'),
 (('CRWV', 'coreweave'), 'The recent pullback in CoreWeave makes the valuation more reasonable.'),
 (('CRWV', 'coreweave'), 'CoreWeave shares bounced off their lows.'),
 (('CRWV', 'coreweave'), 'CoreWeave trimmed early losses.'),
 (('CRWV', 'coreweave'), 'CoreWeave rose for a fifth straight session.'),
 (('CRWV', 'coreweave'), 'CRWV stock is a top performer in 2025.'),
 (('CRWV', 'coreweave'), "CoreWeave's slide deepened on Thursday."),
 (('CRWV', 'coreweave'), 'CoreWeave gave back 4% on Friday.'),
 (('CRWV', 'coreweave'), 'Why Is CoreWeave Stock Down Today?'),
 (('CRWV', 'coreweave'), 'CoreWeave Stock Is Up Big Today'),
 (('CRWV', 'coreweave'), 'CoreWeave has more than tripled from its IPO price.'),
 (('CRWV', 'coreweave'), 'CoreWeave stock is 3x its IPO price.'),
 (('CRWV', 'coreweave'), 'CoreWeave is trading at 2x its IPO price.'),
 (('CRWV', 'coreweave'), 'A $10,000 investment in CoreWeave at its IPO would be worth $45,000 today.'),
 (('CRWV', 'coreweave'), 'CoreWeave sits 35% off its high.'),
 (('CRWV', 'coreweave'), "The stock's 52-week low is $33."),
 (('CRWV', 'coreweave'), 'One share of CoreWeave now sells for about $130.'),
 (('CRWV', 'coreweave'), 'The price of a CoreWeave share is about $130.'),
 (('CRWV', 'coreweave'), 'CoreWeave ended the week in the red.'),
 (('CRWV', 'coreweave'), "CoreWeave's market cap of $65 billion is down from $90 billion in June."),
 (('CRWV', 'coreweave'), 'CoreWeave is now worth $60 billion, down from $90 billion.'),
 (('CRWV', 'coreweave'), 'CoreWeave, a 2025 IPO up 200%, signed a new deal.'),
 (('CRWV', 'coreweave'), 'CoreWeave beat the S&P 500 by 150 points this year.'),
 (('CRWV', 'coreweave'), 'The stock fetched $45 in early trading.'),
 (('CRWV', 'coreweave'), 'CoreWeave pulled back from its record.'),
 (('CRWV', 'coreweave'), 'CoreWeave gapped up at the open.'),
 (('CRWV', 'coreweave'), 'CoreWeave has delivered a 200% return since its IPO.'),
 (('ORCL', 'oracle'), "Oracle's value soared by $250 billion in a day."),
 (('ORCL', 'oracle'), 'Oracle became worth nearly $1 trillion overnight.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin eased 2% overnight.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin traded sideways for most of the week.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Over $1 billion in longs were liquidated as BTC dropped.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'BTC printed a new ATH.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin is 10% off its ATH.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin briefly traded under six figures.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin is back above six figures.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin is in the red for the week.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin hovered.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'ETH advanced while BTC lagged.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'Ether weakened against Bitcoin.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'Ether is trading in the $4,000s.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'ETH set a new ATH above $4,900.'),
 (('SOLUSD', 'SOL', 'Solana'), 'SOL dumped 15% after the hack.'),
 (('SOLUSD', 'SOL', 'Solana'), "Solana's ATH of $293 is still out of reach."),
 (('XRPUSD', 'XRP'), "XRP has 5x'd since November."),
 (('DOGEUSD', 'DOGE', 'Dogecoin'), 'DOGE pumped 30% overnight.'),
 (('DOGEUSD', 'DOGE', 'Dogecoin'), 'Dogecoin is a 10-bagger this year.'),
 (('TRUMPUSD', 'TRUMP', 'Official Trump'), 'The memecoin cratered 60% in a week.'),
 (('TRUMPUSD', 'TRUMP', 'Official Trump'), 'TRUMP got cut in half.'),
 (('NVDA', 'nvidia'), 'Nvidia trades near record territory.'),
 (('TSLA', 'tesla'), 'Tesla shares took it on the chin.'),
 (('AAPL', 'apple'), "Apple's shares are hovering just below their high."),
 (('META', 'meta platforms', 'meta'), 'Meta shares round-tripped after earnings.'),
 (('PLTR', 'palantir'), 'Palantir has gone parabolic.'),
 (('HD', 'home depot'), 'Home Depot gave up its post-earnings gains.'),
 (('MU', 'micron technology', 'micron'), 'Micron shares are on track for a record month.'),
]

# The price-claim twins of round 4's narrowings (must stay flagged). (terms, text)
FLAG_ROUND4_PROBES = [
 (('TXN',), 'Texas Instruments rose 5% on the news.'),
 (('CRWV', 'coreweave'), 'Shares of CoreWeave cooled off after a strong run.'),
 (('TSLA', 'tesla'), 'Tesla shares had their best day since 2021.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), "Bitcoin's ATH of $126,000 came in October."),
 (('CRWV', 'coreweave'), "CoreWeave's 52-week low is $33."),
 (('CRWV', 'coreweave'), "The stock's all-time high of $187 came in June."),
 (('NVDA', 'nvidia'), 'Nvidia moved higher after earnings.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin edged up 1% overnight.'),
 (('TSLA', 'tesla'), 'Tesla edged up.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'The rally in Bitcoin has lifted miners.'),
 (('TRUMPUSD', 'TRUMP', 'Official Trump'), 'The TRUMP memecoin plunged 20% after the dinner.'),
 (('SOLUSD', 'SOL', 'Solana'), 'The Solana token fell 12% after the hack.'),
 (('ETHUSD', 'ETH', 'Ethereum', 'Ether'), 'Ethereum market cap rose to $500 billion.'),
 (('AAPL', 'apple'), 'Its market value of $3 trillion is up from $2 trillion in March.'),
 (('TSLA', 'tesla'), 'Tesla gained on the delivery beat.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'Bitcoin rose against the S&P 500 this year.'),
 (('BTCUSD', 'BTC', 'Bitcoin'), 'BTC traded flat.'),
 (('ORCL', 'oracle'), 'The company became worth $1 trillion on Tuesday.'),
 (('CRWV', 'coreweave'), 'Shares went from $40 to $187.'),
 (('CRWV', 'coreweave'), 'Its stock hit its high on Tuesday.'),
 (('CRWV', 'coreweave'), 'Shares are in the red for the week.'),
 (('CRWV', 'coreweave'), 'Shares ended the week higher.'),
]

# Round-4 price talk left to the prompt. (terms, text, why)
PROMPT_ONLY_ROUND4 = [
    (('ET', 'energy transfer'), 'Energy Transfer units rose 2% after the distribution increase.',
     "an MLP's 'units' are also unit SALES ('Tesla units rose 5% after the price cut')"),
    (('CRWV', 'coreweave'), 'CoreWeave slipped into the red for the year.',
     "'into the red for the year' is also a full-year net loss ('Intel slipped into the red')"),
    (('HIMS', 'hims & hers health'), 'Hims rose 5% on the news.',
     "a sentence-initial capitalised plural ('Visits rose 5% on the week') is a metric to "
     "session_pct; the name rows still catch it when 'hims' is a term"),
]

# Could be honest, but share the anchor's exact shape — flagged on purpose.
FLAGGED_BY_DESIGN = [
    ("MU", "Margins fell on recent price declines.",
     "'recent price declines' with no product named is the CRWV conclusion verbatim"),
    ("AAPL", "The 5% decline was driven by China.",
     "a sentence that opens on an unnamed move is about the price"),
    ("CRWV", "Its valuation fell to 30 times earnings.",
     "a valuation MOVE, even when stated as a multiple"),
]

CRWV_P3 = ("Despite positive news like Nvidia's Vera Rubin NVL72 systems going into production "
           "for Cognition, CoreWeave shares experienced a 2.2% slip.")
CRWV_CONCLUSION = ("Strong demand and analyst support are countered by recent price declines, "
                   "leaving the near-term outlook for CoreWeave uncertain.")
ANCHOR_PASS = [
    "Truist Securities issued a bullish endorsement, citing CoreWeave's pricing power and "
    "co-location advantages.",
    "CoreWeave's neocloud business is experiencing a boom with record backlog levels.",
    "Earnings per share rose 18% to $1.47.",
    "Bitcoin ETF inflows rose 20% this week.",
    "CoreWeave revenue jumped 200% to $1.4 billion.",
    "Its share price target was raised to $250 at Jefferies.",
    "Micron noted recent price declines in NAND would pressure margins.",
]

def _t(scope):
    return tuple(TERMS[scope])


# Every table as (terms, text): the round-1/2 rows name a TERMS scope, round 3 carries its own.
MUST_PASS = (
    [(_t(k), s) for k, s in HONEST_ROUND1 + HONEST_ROUND2 + HONEST_UNDERBLOCK_PASS + HONEST_PROBES]
    + [(_t(k), s) for k, s, _why in RECLASSIFIED_ALLOWED]
    + HONEST_ROUND3 + HONEST_ROUND3_TWINS + HONEST_ROUND4 + HONEST_ROUND4_TWINS
)
MUST_FLAG = (
    [(_t(k), s) for _fam, k, s in FLAG_UNDERBLOCK] + [(_t(k), s) for k, s in FLAG_PROBES]
    + FLAG_ROUND3 + FLAG_ROUND3_PROBES + FLAG_ROUND4 + FLAG_ROUND4_PROBES
)
ALL_PROMPT_ONLY = ([(_t(k), s, why) for k, s, why in PROMPT_ONLY] + PROMPT_ONLY_ROUND3
                   + PROMPT_ONLY_ROUND4)


def _ids(rows):
    return [r[1][:60] for r in rows]


# ── the CRWV anchors ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("terms", [["CRWV", "CoreWeave"], ["CRWV", "coreweave"], ["CRWV"], []])
def test_the_crwv_point_and_conclusion_are_flagged_with_or_without_the_name(terms):
    # the read path has no company name — only the symbol — so the generic rows must do it
    assert price_claims(CRWV_P3, terms) == ["shares experienced a 2.2% slip"]
    assert price_claims(CRWV_CONCLUSION, terms) == ["recent price declines"]


@pytest.mark.parametrize("text", ANCHOR_PASS)
@pytest.mark.parametrize("terms", [["CRWV", "CoreWeave"], ["BTCUSD", "BTC", "Bitcoin"], []])
def test_the_honest_anchors_pass(text, terms):
    assert price_claims(text, terms) == []


# ── gate 1: the must-pass table has ZERO hits ────────────────────────────────

@pytest.mark.parametrize("terms,text", MUST_PASS, ids=_ids(MUST_PASS))
def test_must_pass(terms, text):
    assert price_claims(text, list(terms)) == []


# Head words a careless caller might add. Safe anyway: a name counts only as the verb's
# subject, so "First quarter revenue rose" is never "First rose".
_GENERIC_HEADS = {
    "FSLR": ["first"], "HD": ["home"], "RKLB": ["rocket"], "RKT": ["rocket"],
    "MSTR": ["strategy"], "GM": ["general"], "AXP": ["american"], "LYV": ["live"],
    "PSA": ["public"], "DAL": ["delta"], "AMD": ["advanced"], "ISRG": ["intuitive"],
    "MU": ["micron"], "BAC": ["bank"], "LLY": ["eli"], "TGT": ["target"], "XYZ": ["block"],
}


def _production_shaped(terms):
    """What `news_insight_service.price_terms` can send, at its widest: each name lower-cased,
    its first two words, and its head word (even an ordinary one, as it does today for
    "rocket", "delta", "five", "hong", "walt" — and junk prefixes such as "bank of")."""
    out = []
    for t in terms:
        if t.isupper() or not any(c.isalpha() for c in t):
            out.append(t)
            continue
        words = t.lower().split()
        out += [t.lower(), " ".join(words[:2]), words[0]]
    return list(dict.fromkeys(out))


def test_must_pass_holds_with_production_shaped_terms():
    hits = []
    for terms, text in MUST_PASS:
        scope = next((k for k, v in TERMS.items() if tuple(v) == terms), None)
        wide = _production_shaped(terms) + _GENERIC_HEADS.get(scope, [])
        got = price_claims(text, wide)
        if got:
            hits.append((text, wide, got))
    assert hits == []


# ── gate 2: the must-flag table ─────────────────────────────────────────────

@pytest.mark.parametrize("terms,text", MUST_FLAG, ids=_ids(MUST_FLAG))
def test_must_flag(terms, text):
    assert price_claims(text, list(terms)), text


# The two PROMPT_ONLY rows that came from round 2's must-flag set (the rest of PROMPT_ONLY
# are probes written while building).
_REVIEW_PROMPT_ONLY = ("The fund's NAV fell 1.2%.", "Volatile trading followed the lockup expiry.")


def test_the_reviewers_price_claims_are_caught_above_ninety_percent():
    """Every reviewer row that IS a price claim (rounds 2 and 3, minus the owner's
    reclassifications), with the detector's own verdict counted here — not the table it sits
    in. test_must_flag already proves each FLAG row; this one bounds how much may be moved
    to PROMPT_ONLY (a quiet way to "pass" a gate) and pins the coverage numbers."""
    assert set(_REVIEW_PROMPT_ONLY) <= {s for _k, s, _why in PROMPT_ONLY}
    round4_prompt_only = [(t, s) for t, s, _w in PROMPT_ONLY_ROUND4 if not s.startswith("Hims")]
    rows = ([(_t(k), s) for _f, k, s in FLAG_UNDERBLOCK]
            + [(_t(k), s) for k, s, _w in PROMPT_ONLY if s in _REVIEW_PROMPT_ONLY]
            + FLAG_ROUND3 + [(t, s) for t, s, _w in PROMPT_ONLY_ROUND3]
            + FLAG_ROUND4 + round4_prompt_only)
    caught = sum(1 for terms, s in rows if price_claims(s, list(terms)))
    # round 2: 117 rows = 112 caught + 3 reclassified (allowed) + 2 prompt-only;
    # round 3: 173 misses = 166 caught + 7 prompt-only (the sector-rally row moved 2026-10-09);
    # round 4: 71 misses = 69 caught + 2 prompt-only (the Hims row is a probe, not theirs)
    assert (len(rows), caught) == (114 + 173 + 71, 112 + 166 + 69)
    assert caught / len(rows) >= 0.9


@pytest.mark.parametrize("terms,text,why", ALL_PROMPT_ONLY, ids=[r[1][:50] for r in ALL_PROMPT_ONLY])
def test_prompt_only_is_not_flagged(terms, text, why):
    assert price_claims(text, list(terms)) == [], f"widened on purpose? {why}"


@pytest.mark.parametrize("scope,text,why", FLAGGED_BY_DESIGN,
                         ids=[r[1][:50] for r in FLAGGED_BY_DESIGN])
def test_flagged_by_design(scope, text, why):
    assert price_claims(text, TERMS[scope]), f"narrowed on purpose? {why}"


def test_the_read_path_symbol_only_terms_still_catch_most_claims():
    # The read-time net knows the symbol (and a coin's names), never the company name. The
    # generic rows must carry most of the load there too; names add the rest at write time.
    def symbols(terms):
        return [t for t in terms if t.isupper()] if not any(
            ic._PC_COIN_PAIR.fullmatch(t) for t in terms) else list(terms)
    caught = sum(1 for terms, s in MUST_FLAG if price_claims(s, symbols(terms)))
    # 305 / 353 after round 3; 380 / 444 after round 4 (its new catches are mostly the
    # write path's: "CoreWeave gave back 4%" needs the name)
    assert caught / len(MUST_FLAG) >= 0.85, caught


# ── the subject's own names ─────────────────────────────────────────────────

def test_a_ticker_matches_case_sensitively_and_a_name_only_capitalised():
    assert price_claims("NET rose 5% after the deal.", ["NET", "Cloudflare"])
    assert price_claims("Net revenue rose 5% after the deal.", ["NET", "Cloudflare"]) == []
    assert price_claims("ON fell 4% on the guidance.", ["ON", "onsemi"])
    assert price_claims("Spending on chips fell 4%.", ["ON", "onsemi"]) == []
    assert price_claims("COREWEAVE JUMPS 8%", ["CRWV", "coreweave"])
    assert price_claims("coreweave jumped 8% today.", ["CRWV", "coreweave"]) == []


def test_symbols_that_are_prose_never_count_alone():
    # "A" is the article, "AI" and "IT" are everyday acronyms: the name still counts
    assert price_claims("A surge after the pause lifted orders 12%.", ["A", "agilent"]) == []
    assert price_claims("Agilent surged 12% after the pause.", ["A", "agilent"])
    assert price_claims("AI surged after the launch, lifting bookings.", ["AI", "c3.ai"]) == []
    assert price_claims("C3.ai surged after the launch, lifting bookings.", ["AI", "c3.ai"])


def test_name_forms_small_words_apostrophes_and_dot_com():
    assert price_claims("Bank of America fell 3% on the news.", ["BAC", "bank of america"])
    assert price_claims("Lowe’s fell 3% on the guidance.", ["LOW", "lowe's"])
    assert price_claims("Lowe's fell 3% on the guidance.", ["LOW", "lowe’s"])
    assert price_claims("Amazon fell 3% after AWS growth missed.", ["AMZN", "amazon.com"])
    assert price_claims("Amazon.com fell 3% after AWS growth missed.", ["AMZN", "amazon.com"])
    assert price_claims("Amazon-backed Anthropic rose 3% in valuation.", ["AMZN", "amazon.com"]) == []


def test_a_name_after_a_preposition_or_quantity_word_is_not_the_subject():
    assert price_claims("Revenue at CoreWeave rose 200%.", ["CRWV", "CoreWeave"]) == []
    assert price_claims("Staked ETH rose 5% to 35 million.", ["ETHUSD", "ETH", "Ethereum"]) == []
    assert price_claims("Analysts see Bitcoin reaching $200,000 by 2026.", ["BTC", "Bitcoin"]) == []
    assert price_claims("Bitcoin reached $100,000.", ["BTC", "Bitcoin"])
    assert price_claims("Miners sold 5,000 BTC at about $110,000 each.", ["BTCUSD", "BTC"]) == []


def test_coin_rows_need_a_coin_card():
    # "its price" / "the price of X" are the coin's only on a coin card (a pair symbol)
    assert price_claims("Its price fell 8% after the unlock.", ["SOLUSD", "SOL", "Solana"])
    assert price_claims("Its price fell 8% after the unlock.", ["NFLX", "netflix"]) == []
    assert price_claims("Netflix's price rose to $17.99 for the standard plan.",
                        ["NFLX", "netflix"]) == []
    assert price_claims("Bitcoin's price rose to $117,000.", ["BTCUSD", "BTC", "Bitcoin"])
    coin = {n for n, _ in price_term_patterns(["BTCUSD", "BTC", "Bitcoin"])}
    equity = {n for n, _ in price_term_patterns(["CRWV", "coreweave"])}
    assert {n for n in coin - equity} == {n for n, _ in ic._PC_COIN_ROWS}


def test_coin_nickname_and_parenthesised_symbol():
    assert price_claims("Ether slid 8% over the weekend.", ["ETHUSD", "ETH", "Ethereum"])
    assert price_claims("CoreWeave (CRWV) rose 6 percent.", ["CRWV"])
    assert price_claims("CoreWeave (NASDAQ: CRWV) rose 6%.", ["CoreWeave"])


def test_names_are_the_only_thing_terms_add():
    text = "CoreWeave Rallies on Microsoft Deal"
    assert price_claims(text) == []
    assert price_claims(text, ["CRWV", "CoreWeave"]) == ["CoreWeave Rallies"]


# ── output shape ────────────────────────────────────────────────────────────

def test_phrases_come_in_text_order_deduplicated_without_overlaps():
    text = ("Bitcoin fell 3% as ETF outflows mounted; shares rose. Despite the rally, "
            "shares rose.")
    assert price_claims(text, ["BTC", "Bitcoin"]) == ["Bitcoin fell 3%", "shares rose", "the rally"]
    # three rows match inside one span; the earliest, longest one is reported once
    assert price_claims("CoreWeave's 2.2% slip came after a downgrade.", ["CRWV", "CoreWeave"]) \
        == ["CoreWeave's 2.2% slip"]


@pytest.mark.parametrize("text", [None, "", "   ", 123, 4.5, b"shares fell 5%", ["shares fell"],
                                  {"t": "shares fell"}, object()])
def test_never_raises_on_odd_text(text):
    assert price_claims(text) == []
    assert price_claims(text, ["CRWV"]) == []


@pytest.mark.parametrize("terms", [None, 123, "CRWV", b"CRWV", [None, 5, "", "  ", "CRWV"],
                                   ("x" * 500,), (t for t in ["CRWV", "CoreWeave"]),
                                   {"CRWV": 1}, ["the", "shares", "(CRWV)", "$", "C3.ai"],
                                   ["BTCUSD"], ["A"], ["'", "’s", ".com", "a.com"]])
def test_odd_terms_never_raise(terms):
    assert price_claims("Revenue rose 5%.", terms) == []
    assert price_claims("Shares fell 5%.", terms) == ["Shares fell"]


# ── gate 3: every row carries its own weight ────────────────────────────────

# One hand-written sample per row that ONLY that row matches. A new row without one fails
# below; so does a row whose sample a sibling also catches (then neither proves anything).
ROW_SAMPLES = {
    "subject_move": ("CRWV", "Shares of CoreWeave rose 6% on the Microsoft deal."),
    "subject_relative_move": ("CRWV", "Shares, which fell 30% last month, recovered some ground."),
    "coin_move": ("SOL", "The coin is down 12% this week."),
    "descriptor_move": ("CRWV", "The chipmaker fell sharply after the guidance."),
    "share_price_noun": ("CRWV", "Share price weakness weighed on sentiment."),
    "price_noun": ("CRWV", "The strategy was countered by recent price declines."),
    "price_noun_stock": ("CRWV", "Contract wins are offset by price weakness."),
    "possessive_move": ("CRWV", "The stock's sharp run-up raises the bar for the next report."),
    "move_in_shares": ("CRWV", "The sell-off in CoreWeave stock deepened."),
    "stock_noun": ("CRWV", "Recent stock weakness reflects financing worries."),
    "stock_performance": ("CRWV", "The backlog story contrasts with mixed stock performance."),
    "caused_move": ("ORCL", "Questions about client revenue weighed, causing stock to fall."),
    "sector_move": ("CRWV", "AI infrastructure names lagged behind as the sector rallied."),
    "group_stocks_move": ("CRWV", "Software stocks are rallying on rate-cut hopes."),
    "streak": ("BTC", "Bitcoin extended its losing streak to five days."),
    "pared_gains": ("CRWV", "CoreWeave pared losses after the upgrade."),
    "session_gains": ("CRWV", "Intraday gains faded by the close."),
    "trading_pressure": ("CRWV", "Selling pressure followed the convertible-note offering."),
    "traders_shares": ("CRWV", "Investors bid up the stock after the deal."),
    "causative_shares": ("CRWV", "The downgrade weighed on the stock."),
    "best_worst_day": ("CRWV", "It posted its best day since March."),
    "the_pct_move": ("CRWV", "Despite the 5% drop, analysts stayed bullish."),
    "ipo_price": ("CRWV", "The stock remains below its IPO price."),
    "vs_index": ("CRWV", "It underperformed the S&P 500 this year."),
    "digit_move": ("CRWV", "A double-digit percentage drop followed the guidance."),
    "ytd_gains": ("CRWV", "Gains of 40% year to date reflect AI enthusiasm."),
    "session_pct": ("CRWV", "It rose 3% in after-hours trading."),
    "the_rally": ("CRWV", "Despite the rally, debt remains a concern."),
    "market_value_move": ("CRWV", "Its market value swelled to $70 billion."),
    "wiped_value": ("ORCL", "The report wiped $20 billion off Oracle's market value."),
    "became_size": ("CRWV", "CoreWeave became a $100 billion company."),
    "level_phrase": ("CRWV", "The last trade was $128."),
    "performance_rank": ("OPEN", "Opendoor led gainers on the Nasdaq on Monday."),
    "rival_move": ("NVDA", "Rival Nebius rose 8%."),
    "term_move_magnitude": ("CRWV", "CoreWeave Gains 4% on New Microsoft Deal"),
    "term_move_intransitive": ("CRWV", "CoreWeave Rallies on Microsoft Deal"),
    "term_signed_pct": ("CRWV", "CRWV +6% after the Nvidia stake disclosure."),
    "term_possessive_move": ("BTC", "Bitcoin's rally stalled near resistance."),
    "term_level": ("BTC", "Bitcoin dipped below $60,000."),
    "term_appositive_move": ("CRWV", "CoreWeave, down 40% from its high, announced a buyback."),
    "term_causative": ("BTC", "The news sent Bitcoin higher."),
    "term_valuation_move": ("CRWV", "CoreWeave's valuation surged past $100 billion."),
    "term_market_value": ("NFLX", "Netflix lost $40 billion in market value."),
    "term_vs_index": ("PLTR", "Palantir has outpaced the Nasdaq this year."),
    "coin_price_subject": ("BTC", "The price of Bitcoin fell 4% overnight."),
    "coin_price_noun": ("ETH", "Price action was choppy after the Fed decision."),
    "coin_level": ("BTC", "Bitcoin Eyes $120K as Inflows Return"),
    "coin_best_period": ("BTC", "Bitcoin posted its worst quarter since 2022."),
    "term_soft_move": ("BTC", "Bitcoin hovered."),
    "term_move_in": ("CRWV", "The recent pullback in CoreWeave makes the valuation more reasonable."),
}


def _rows(terms):
    return list(PRICE_CLAIM_PATTERNS) + price_term_patterns(terms)


def test_every_row_has_a_sample_and_every_sample_a_row():
    # a coin card compiles every row (the coin rows on top of the name rows)
    names = [n for n, _ in _rows(["BTCUSD", "BTC", "Bitcoin"])]
    assert len(names) == len(set(names)), "row names must be unique"
    assert set(names) == set(ROW_SAMPLES)


@pytest.mark.parametrize("name", sorted(ROW_SAMPLES))
def test_each_rows_sample_is_matched_by_that_row_alone(name):
    scope, sample = ROW_SAMPLES[name]
    assert ic.price_claim_rows(sample, TERMS[scope]) == [name]


@pytest.mark.parametrize("name", sorted(ROW_SAMPLES))
def test_removing_a_row_loses_its_sample(name, monkeypatch):
    """The scan really applies every row: deleting one (what a careless edit does) makes
    its sample pass."""
    scope, sample = ROW_SAMPLES[name]
    assert price_claims(sample, TERMS[scope])
    monkeypatch.setattr(ic, "PRICE_CLAIM_PATTERNS",
                        [r for r in ic.PRICE_CLAIM_PATTERNS if r[0] != name])
    monkeypatch.setattr(ic, "_PC_TERM_ROWS", [r for r in ic._PC_TERM_ROWS if r[0] != name])
    monkeypatch.setattr(ic, "_PC_COIN_ROWS", [r for r in ic._PC_COIN_ROWS if r[0] != name])
    ic._term_patterns.cache_clear()
    try:
        assert price_claims(sample, TERMS[scope]) == []
    finally:
        ic._term_patterns.cache_clear()


def test_the_verb_guard_admits_every_listed_word():
    # the first-word guard must never hide a verb the lists contain
    guard = re.compile(ic._PC_VERB_GUARD, re.IGNORECASE)
    for word in ic._PC_VERB_FIRST:
        assert guard.match(word + " "), word


def test_the_verb_guard_never_changes_a_verdict():
    """The other direction: a verb alternative whose first word is missing from the trie
    would be silently dead. Compiled WITHOUT the guard, subject_move must give the same
    verdict on every table row and every hostile-free probe."""
    unguarded = re.compile(ic._PC_SUBJ + ic._PC_GAP + rf"(?:{ic._PC_VERB_CORE})(?![\w-])",
                           re.IGNORECASE)
    guarded = dict(PRICE_CLAIM_PATTERNS)["subject_move"]
    texts = [s for _t, s in MUST_FLAG + MUST_PASS] + [s for _t, s, _w in ALL_PROMPT_ONLY]
    texts += [s for _k, s in ROW_SAMPLES.values()]
    diff = [s for s in texts if bool(unguarded.search(s)) != bool(guarded.search(s))]
    assert diff == []


# ── gate 4: linear time on hostile input ────────────────────────────────────

_N = 20_000
HOSTILE = {
    "shares": "shares " * _N,
    "stock_comma": "stock, " * _N,
    "dollars": "$1 " * _N,
    "dollars_glued": "$1" * _N,
    "dollars_comma": "$1," * _N,
    "digits": "9" * _N,
    "percent": "1% " * _N,
    "comma_chain": "shares" + ", x" * _N,
    "names": "CoreWeave's " * _N,
    "ticker": "CRWV " * _N,
    "price": "recent price " * _N,
    "rally_in": "rally in " * _N,
    "mixed": "Shares of CoreWeave, which, rose " * (_N // 5),
    # one long whitespace run after a subject: two adjacent \s* once made rows quadratic
    "ticker_spaces": "CRWV" + " " * _N + "x",
    "amount_spaces": "wiped $1" + " " * _N + "x",
    "shares_spaces": "shares" + " " * _N + "+x",
    "possessive_spaces": "Bitcoin's" + " " * _N + "x",
    # round 3: the new rows' own shapes, repeated
    "best_day": "its best day since " * (_N // 4),
    "streak_of": "winning streak of " * (_N // 3),
    "the": "the " * _N,
    "ing_word": "a" * _N + "ing",
    "ticker_paren": "CRWV (" * (_N // 2),
    "market_value": "market value " * (_N // 2),
    "a_dot": "a." * _N,
    "up_more_than": "up more than " * (_N // 3),
    "price_adj": "recent sharp " * (_N // 2) + "price declines",
    "level_preps": "closed at near around " * (_N // 4),
    "title_shares": "Oracle Shares " * (_N // 2),
    "coin_price_of": "the price of Bitcoin " * (_N // 4),
    "with_coin": "With Bitcoin " * (_N // 2),
    "highest": "its highest " * (_N // 2),
    "funding_scan": "its valuation rose " * (_N // 3),
    "for_scan": "best week " + "x " * _N,
    # round 4: the new rows' and guards' own shapes, repeated
    "shares_worth": "shares worth about " * (_N // 3),
    "stock_at": "stock at " * (_N // 2),
    "moves_up": "Delta moves up " * (_N // 3),
    "in_the": "in the " * (_N // 2),
    "cap_words": "Visits Claims Copper " * (_N // 3),
    "coin_costs": "Ethereum costs about " * (_N // 3),
    "investment_in": "a  investment in " * (_N // 4),
    "share_of": "one share of " * (_N // 3),
    "price_of_a": "the price of a " * (_N // 4),
    "ipo_up": "IPO up " * (_N // 2),
    "named_hl": "its 52-week high of " * (_N // 4),
    "while_cap": "ETH advanced while BTC " * (_N // 4),
    "again_comma": "outpaced Ethereum again, " * (_N // 3),
    "pullback_in": "the recent pullback in CoreWeave " * (_N // 5),
    "the_token": "the TRUMP token " * (_N // 3),
    "it_had_its": "it had its " * (_N // 3),
    "price_aside": "recent price declines, especially " * (_N // 4),
    "count_tail": "shares rose to " * (_N // 3),
    "from_to": "shares went from  to " * (_N // 5),
    "lower_ing": "lagged Bitcoin, " + "a" * _N,
}


@pytest.mark.parametrize("label", sorted(HOSTILE))
def test_every_row_is_linear_on_hostile_input(label):
    text = HOSTILE[label]
    terms = ["BTCUSD", "BTC", "Bitcoin", "CRWV", "CoreWeave", "ETH", "Ether"]
    # the name rows run over the sentinel-swapped text, exactly as price_claims scans it
    swapped = ic._swap_terms(text, ic._clean_terms(terms))[0]
    generic = {n for n, _ in PRICE_CLAIM_PATTERNS}
    for name, pat in _rows(terms):
        target = text if name in generic else swapped
        start = time.perf_counter()
        for _ in pat.finditer(target):
            pass
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"{name} took {elapsed:.2f}s on {label!r}"
