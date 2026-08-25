# FMP — reply to Laith (2026-08-24)

Short version, sent in place of the longer draft. Full reasoning and call-site traces:
`~/.claude/plans/check-launch-checklist-md-i-have-sprightly-tarjan.md`.

## Decisions taken

- **Transcripts:** drop. Only 2 consumers, both already on shipped fallbacks.
- **Bandwidth:** ask for **150 GB**, not 100. Trailing 30d is 25.57 GB but that is *pre-launch*
  (dev traffic only). 100 GB is ~4× headroom for "low thousands of users"; 150 GB is ~6×. The tier
  price gap is small, the risk gap is not — a hard stop mid-month is an outage for every paying
  user, and month-to-month means we can negotiate down later. Caching is already optimised
  (batch-quote fix: cold Home ~203 → ~5 calls; ETF/index/commodity caches landed), so there is
  little slack left if we undershoot.
- **Real-time / streaming:** drop both. Never needed, and streaming is what triggers the
  $1,500–2,000/mo exchange fees.
- **Quote endpoint: DO NOT agree to drop.** `/stable/quote` + `/stable/batch-quote` are 16 and 12
  backend callers, and indices / commodities / crypto all ride those same two paths — dropping
  them removes every current price on every asset class.

## The three things to settle before confirming

1. Is "the quote endpoint" the same line item as "the real-time endpoint"? He used both phrases
   interchangeably; *endpoint* and *latency* are different things.
2. Can `quote` / `batch-quote` be served **15-minute delayed** without exchange licences? He
   priced only real-time and skipped straight to removal.
3. ⚠️ His email #2 said the separate Data Display and Licensing Agreement isn't required
   *"provided we remove the quote endpoint."* **If we keep it, does that waiver come back?** This
   gates the App Store **Content Rights** answer — do not answer it in ASC until this is in writing.

Plus: the streaming correction (we told him we consume no streaming feed; `live_price_manager.py:30-31`
says otherwise), and the Advanced Market Metrics entitlement check (5 endpoints called today at
`fmp.py:732, 827, 850, 859, 868` — a 403 risk in production).

Deferred to the Order Form review, not worth asking now: notice period, cancellation access, the
ToS §6.3 vs exported-PDF tension, index/commodity symbol pricing, mixed-batch 403 behaviour.

---

## The email

Subject: Re: Caydex — Enterprise scope

Hi Laith,

Thanks — that's clear, and month-to-month helps a lot.

Confirming two of the three:

1. Earnings call transcripts — yes, please remove them.

2. Bandwidth — yes, please reduce it. 150 GB / 30 days would suit me. My trailing 30-day usage is
   25.57 GB today, but that's pre-launch, so I'd rather have headroom than the tightest number.
   Could you tell me what happens if I hit the cap — throttle, overage charge, or hard stop? If
   it's a hard stop I'd rather stay at 150; if there's an overage rate I could go lower.

3. The quote endpoint — this is the one I can't agree to yet, and I think we may be talking about
   two different things.

I don't need real-time, and I'm happy to drop the real-time entitlement and the exchange fees with
it. But /stable/quote and /stable/batch-quote are load-bearing for my app: they are how I price
everything on screen, and because indices (^GSPC), commodities (GCUSD) and crypto (BTCUSD) all come
back from those same two endpoints, losing them would remove every current price in the product,
not just stock prices.

So before I confirm:

  (a) Is "the quote endpoint" the same thing as "the real-time endpoint" in your pricing, or are
      they separate line items?

  (b) Can /stable/quote and /stable/batch-quote be provided on a 15-minute delayed basis, without
      the exchange licences? My Terms of Use already disclose a 15–20 minute delay, so delayed is
      fine for me — this is a research app, not a trading app.

  (c) In your earlier email you said a separate licensing agreement isn't required provided we
      remove the quote endpoint. If we keep it on a delayed basis, does that change?

One correction I owe you: I said previously that we don't consume a streaming feed. That was wrong.
My backend does have a WebSocket client for websockets.financialmodelingprep.com and
crypto.financialmodelingprep.com. It is inert in practice — my current key isn't entitled and both
sockets return 401 — but the code is there, and I'd rather tell you now than have it surface later.
Please leave streaming out of the package and I'll remove the client before launch.

One last small thing: you mentioned advanced market metrics weren't in the original scope. I do
call sector-performance-snapshot, industry-performance-snapshot, biggest-gainers, biggest-losers
and most-actives today — are those entitled under the plan, or will they stop working? Happy to
remove them if not.

Once (a)–(c) are clear I can confirm everything else and we can move to the formal quote.

Best regards,
Hayden
