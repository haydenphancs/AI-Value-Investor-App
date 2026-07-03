# Content Tech Inventory — Caydex / AI Value Investor

> **Purpose.** A neutral catalog of the technology already in this codebase that can produce
> or serve *content* — AI-written research prose, narrated audio, synced read-along, financial
> data hooks, and visual/UI assets. This is a reference inventory, not a marketing plan. Each
> entry states what the system is, where it lives, and what artifact it emits.
>
> **Scope note.** Paths are relative to the repo root. Line-number citations are hints — verify
> against current code before quoting. This doc is a point-in-time snapshot (authored 2026-07-03).
> Companion architecture docs live in [SYSTEM_DESIGN_GUIDELINES.md](SYSTEM_DESIGN_GUIDELINES.md).

---

## 0. Stack at a glance

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Deployed to **Railway** (CPU-only) via `Procfile` |
| Database | Supabase (Postgres + pgvector) | No ORM; raw Supabase Python SDK |
| Object storage | Supabase Storage (public + private buckets) | Serves audio + PDFs |
| LLM | Google Gemini (`gemini-2.5-flash*` / `-pro`) | Surfaced only as **"Cay AI by Caydex"** — see §8 |
| TTS (cloud) | Gemini `gemini-2.5-flash-preview-tts`, voice **Achird** | Learn-content narration |
| TTS (local clone) | **Chatterbox TTS** (MIT license) on **RunPod RTX 4090** | Consistent single-voice cloning |
| Forced alignment | torchaudio **MMS_FA** (Meta MMS, free/local) | Read-along timings |
| Financial data | Financial Modeling Prep (FMP) `/stable` | Primary market/fundamentals source |
| Other data | FRED, CoinGecko, FINRA, ApeWisdom, Alternative.me | Macro, crypto, short interest, social |
| Frontend | SwiftUI native iOS (dark theme, Atomic Design) | Built via `xcodebuild` |

The two work zones are independent (not a monorepo): `backend/` (FastAPI → Railway) and
`frontend/ios/ios/` (SwiftUI → Xcode).

---

## 1. AI content-generation engines

The core "content factory" is the multi-agent research pipeline. Given a ticker, it produces a
full structured report of AI-written, persona-styled prose plus deterministic numeric sections.

### 1.1 Multi-agent research pipeline

Directory: `backend/app/services/agents/`

| File | Role |
|---|---|
| `research_agent.py` | Top-level orchestrator — 5-phase pipeline (collect → agentic research → Stage A → assemble → Stage B) |
| `ticker_report_data_collector.py` | **Stage A** data collection: ~20 parallel FMP calls, metric computation, deterministic real-data sections |
| `fmp_tools.py` | Gemini function-calling tool declarations + handlers for the autonomous agentic loop |
| `persona_config.py` | The five investor personas (system prompts, lenses, scoring rules) |
| `persona_scoring.py` | Deterministic 0–100 `quality_score` per persona |
| `narrative_prompts.py` | **Stage B** narrative jobs — one prompt per human-readable prose field |
| `card_verdict.py` | Deterministic (no-LLM) verdict lines under the 4 Fundamentals cards |

**Pipeline phases** (`research_agent.py`, `ResearchAgent.run()`):
1. **Collect** — `TickerReportDataCollector.collect()` fans out FMP + analyst/holders calls and
   pre-builds deterministic numeric sections.
2. **Agentic deep research** — Gemini runs up to `MAX_AGENTIC_ROUNDS = 4` rounds of autonomous
   FMP tool-calling, returning a free-text synthesis (stored as `research_reports.full_report`).
   This is the credit-gated "depth premium."
3. **Stage A** — one Gemini JSON call for scoring/categorization only (narrative slots empty).
4. **Assemble** — `assemble_report()` merges deterministic numeric data with the Stage-A shell
   (real data wins over model output).
5. **Stage B** — N parallel `gemini.generate_text` calls write persona-styled prose per field,
   concurrently with bull/bear thesis synthesis.

### 1.2 The five personas

Defined in `persona_config.py` as `PersonaConfig` objects; keys in `PERSONA_KEYS`.

| Key | iOS tag | Lens |
|---|---|---|
| `warren_buffett` | `buffett` | Quality/value — durable moats, owner earnings, ROE, margin of safety |
| `cathie_wood` | `wood` | Disruptive-innovation growth — TAM, revenue acceleration, S-curves |
| `peter_lynch` | `lynch` | GARP — six stock categories, PEG, "tenbaggers," the story |
| `bill_ackman` | `ackman` | Concentrated activist — FCF quality/yield, downside protection, catalysts |
| `michael_burry` | `burry` | Deep-value contrarian — balance-sheet forensics, penalizes hype |

Each writes in-character prose and can score the **same ticker differently** (weights in
`PERSONA_WEIGHTS` + a bounded `style_fit_adjustment` of ±10). Adding a persona is a documented
playbook (`.claude/skills/add-research-persona/`).

### 1.3 Human-readable prose fields (Stage B output)

`build_narrative_jobs()` in `narrative_prompts.py` emits one job per field. The prose blocks
produced per report (with word caps) include:

- `executive_summary_text` (~80 words) and `executive_summary_bullets`
- `overall_assessment.text` — Fundamentals & Growth verdict (~90 words)
- `core_thesis.bull_case` / `core_thesis.bear_case` (via `synthesize_core_thesis`)
- `moat_durability_note`, `moat_competitive_insight`
- `macro_intelligence_brief`, `price_action_narrative`
- `revenue_engine_analysis_note`, `revenue_forecast_insight`
- `hidden_market_signals_insight` (congress + short-interest synthesis, ≤130 words)
- `key_management_insight`, `insider_key_insight`, `wall_street_insight`
- `critical_factors[i].description` + `.watch` (forward-looking triggers)

### 1.4 Deterministic text generators (no LLM)

These emit human-readable text purely from computed numbers — cheap, reproducible, no quota:

- `agents/card_verdict.py` → `generate_card_verdict()`: short verdict + sentiment under each of
  the 4 Fundamentals cards, from a fixed vocabulary (e.g. "Fat Margins vs Industry," "Surging
  Earnings," "Cheaper Than Sector," "Bankruptcy Risk," "Burning Cash").
- `narrative_prompts.py` helpers: `_fundamentals_trajectory_block`, `_metric_trajectory_line`,
  the `_digest_*` family → `build_module_digest` (number-anchored trend bullets).
- Other scored/verdict services: `services/signal_of_confidence_service.py`,
  `services/portfolio_insights_service.py`, `services/moat_scoring_service.py`,
  `earnings_service._compute_surprise` (signed beat/miss %, `beat_summary` like "Beat 6 of 8").

### 1.5 Report content store & export

| Store | Path / table | Contents |
|---|---|---|
| Report record | Supabase `research_reports` (via `services/research_service.py`) | `ticker_report_data` JSONB (full report) + denormalized columns: `title`, `executive_summary`, `full_report`, `investment_thesis`, `pros`, `cons`, `moat_analysis`, `valuation_analysis`, `risk_assessment`, `key_takeaways`, `action_recommendation`, `overall_score` |
| Report cache | Supabase `ticker_report_cache` (`services/ticker_report_cache.py`) | Full `TickerReportResponse`, keyed `(ticker, persona)`, 24h TTL |
| Data cache | Supabase `ticker_data_cache` (`services/ticker_data_cache.py`) | Persona-neutral collected data (24h) so personas 2..N skip FMP fan-out |
| Response schema | `backend/app/schemas/ticker_report.py` (`TickerReportResponse`) | Canonical report shape iOS decodes |

**PDF export** — `services/pdf_report_service.py`: renders the frozen `ticker_report_data`
through Jinja2 templates (`backend/app/templates/pdf/`) → **WeasyPrint** → a multi-page branded
PDF, uploaded to the **private** `research-pdfs` Storage bucket. Charts are pre-rendered to SVG
by `services/pdf_charts.py`. Pure deterministic render — no new LLM/FMP calls.

---

## 2. Financial data sources & signal engines

These produce structured, number-anchored data points (distinct from prose). All flow through
the cache-aside two-tier pattern (in-memory dict + Supabase `*_cache` table + `_inflight` dedup).

### 2.1 Whale registry & 13F tracking

- **Seed:** `backend/data/whale_registry.json` — 59 entries (33 investors, 18 institutions,
  8 politicians) seeding the Supabase `whales` table at startup. Each has `name`, `title`, a
  descriptive `description`, `category`, `cik`, `data_source` (`"13f"`), `associated_ticker`.
- **Service:** `services/whale_service.py` (+ `_whale_common.py`; endpoint
  `api/v1/endpoints/whales.py`). Dual-source: funds → FMP 13F endpoints; politicians → FMP
  Congressional Trading endpoints. Emits per-whale holdings, trade groups (buy/add/sell), sector
  allocation, and behavior summaries.

### 2.2 Congressional trades ("App-Exclusive Signals")

- `services/signals_service.py` — `_aggregate_congress()` ranks tickers by **distinct** congress
  members who bought (30-day disclosure window). Source: FMP `senate-latest` + `house-latest`.
  Cached in Supabase `signals_cache` (24h). Detail rows surface who bought, when, and amount
  ranges (`format_amount_range`).
- Per-ticker congress activity also feeds the report via
  `holders_service._build_congress_activities` / `_build_congress_smart_money`.
- **Note on naming:** the backend brands this "Congressional Buys" / "App-Exclusive Signals";
  there is no literal "Washington Whales" string in code.

### 2.3 Home dashboard signals

`signals_service.py` powers three home cards:
- **Congressional Buys** — FMP senate/house-latest.
- **Whale Accumulation** — tickers 13F funds are adding to (deduped by CIK), from the
  daily-hydrated Supabase whale tables (no FMP calls at read time).
- **Earnings Shockers** — biggest EPS beats/misses vs Street (signed surprise %), FMP
  `earnings-calendar` via `_compute_surprise`.

### 2.4 Hidden market signals (in-report)

`HiddenMarketSignalsResponse` (`schemas/ticker_report.py`) combines `congress` (num_buyers/
sellers, $ buys/sells, net_direction) + `short_interest` (percent_of_float, days_to_cover,
shares_short, history). The prose synthesis is `hidden_market_signals_insight` (§1.3).

### 2.5 Other data engines

- `services/earnings_service.py` — surprise computation, fiscal-quarter labeling, `beat_summary`,
  earnings timeline.
- `services/geopolitical_macro_service.py` — web-search-grounded (Gemini) scan of real current
  macro/geopolitical shocks; emits risk factors with severity tiers → `macro_data`
  (`overall_threat_level`, `headline`, `intelligence_brief`) + persisted citations.

---

## 3. Audio production pipeline

A largely **free/local** pipeline that turns authored text into narrated, read-along audio.
Generation/alignment/seeding scripts live in `backend/scripts/`.

### 3.1 TTS engine A — Gemini "Achird" (cloud)

| Script | Produces |
|---|---|
| `backend/scripts/generate_money_moves_audio.py` | One narration per Money Moves article → `backend/data/money_moves_audio/<slug>.m4a` |
| `backend/scripts/generate_journey_audio.py` | One `.m4a` per Journey card → `backend/data/journey_audio/<audioClip>.m4a` |
| `backend/scripts/gen_voice_samples.py` | Voice-casting tool — renders short samples of many Gemini voices |

- Model `gemini-2.5-flash-preview-tts`, single voice **`Achird`**, normalized to **~170 WPM**
  (ffmpeg `atempo` post-synthesis). Text chunked ≤1800 chars/call, PCM concatenated.
- **Style steering** via a prompt prefix: Money Moves = "sharp, engaging financial storyteller";
  Journey = "patient, trusted financial mentor."
- This is the **only** step consuming API quota (20s throttle, 429 backoff to 120s — tuned for
  free tier). Resumable: skips items whose `.m4a` already exists.
- Known weakness: Gemini prebuilt voices **drift in timbre per call** — the reason engine B exists.

### 3.2 TTS engine B — Chatterbox voice clone (local, RunPod GPU)

**Chatterbox TTS** (`chatterbox.tts.ChatterboxTTS`, MIT license, commercial-OK) clones a single
reference `.wav` so the voice stays consistent across a whole piece. Isolated env:
`backend/venv_clone` (`backend/requirements_clone.txt`, `backend/Dockerfile`). 24 kHz, AAC/M4A.

| Script | Purpose |
|---|---|
| `backend/scripts/generate_book_audio_clone.py` | Per-book cloned narration; per-core checkpoint/resume → `data/book_audio/<order>_<slug>.m4a` + `.manifest.json` |
| `backend/scripts/clone_learn_audio.py` | Re-clones Learn content (Journey + Money Moves) in one consistent voice, speaking identical text so existing read-along stays valid |
| `backend/scripts/clone_prototype.py` | Earlier prototype |

- **Reference voices** live in `backend/data/voice_clone/refs/`. Books map per-book refs in the
  `REFS` dict inside `generate_book_audio_clone.py`. The Learn "house voice" is
  `caydex_voice_achird_v2.wav` — a single expressive clip **cloned from the app's own Achird
  narration**. Approved settings: `exaggeration=0.65`, `cfg_weight=0.40`, **~165 WPM**.
  Two modes: `block` (per-paragraph, approved) and `sentence` (legacy).

**GPU infra** — `backend/scripts/runpod/`:
- `README.md` — runbook. Rented **RunPod RTX 4090** pod, volume at `/workspace`, ~**$0.34/hr**,
  **~25–40 min/book (~$0.15–0.25/book)**. Railway is CPU-only and cannot run Chatterbox; it stays
  the home for the Gemini-API audio. The pod needs no secrets (reads committed text + ref `.wav`).
- `setup_runpod.sh` (provision), `sync_up.sh` / `sync_down.sh` (rsync Mac ↔ pod),
  `time_and_cost.py` (cost meter → `runpod_cost_log.jsonl`).

### 3.3 Loudness / speed normalization

- `backend/scripts/normalize_book_audio.py` — evens per-core loudness via **gated RMS (dBFS)**,
  applies one linear gain per core (reductions only → never clips). **Sample-exact /
  duration-preserving**, so baked seek offsets and read-along timings stay valid (no re-align).
  Backs up originals to `data/book_audio/orig/`; reversible and re-run safe. (Gated-RMS leveling,
  not true EBU R128 LUFS.)
- `backend/scripts/normalize_book_speed.py` — companion tempo/speed normalizer.

### 3.4 Forced alignment / read-along

Tool: torchaudio **`pipelines.MMS_FA`** (Meta MMS CTC aligner) — **free, local, no API key**;
model auto-downloads on first run. Shared core: `backend/scripts/_forced_align.py`.

| Script | Granularity | Writes |
|---|---|---|
| `align_money_moves_audio.py` | **Sentence** | `readAlong` (per block) + `itemsReadAlong` (per bullet), absolute times; refreshes `audioDurationSeconds` |
| `align_journey_audio.py` | **Word** | `readAlongWords` — one `{text,start,end}` per whitespace token, clip-relative |
| `align_book_audio.py` (+ `gen_book_read_along.py`) | Sentence, per-core | Book Library timings → emits `frontend/ios/ios/Models/BookReadAlong.swift` |

Alignment runs against the **exact audio bytes users hear** (a public-bucket downloader fetches
missing clips first). Timings are embedded in the served JSONB for Money Moves / Journey; baked
into Swift for Books.

---

## 4. Content management & publishing (Learn system)

Three content formats, each with a source-of-truth JSON, a serve endpoint, a seed script, and a
public Storage bucket.

| Type | Source (committed) | Serve endpoint | Seed script → table | Bucket |
|---|---|---|---|---|
| **Money Moves** (articles) | `frontend/ios/ios/Resources/MoneyMoves/money_moves.json` | `GET /api/v1/learn/money-moves` | `seed_money_moves.py` → `money_move_articles` | `money-moves-media` |
| **Investor Journey** (lessons) | `frontend/ios/ios/Resources/Journey/journey_lessons.json` | `GET /api/v1/learn/journey` | `seed_journey.py` → `lessons` | `journey-media` |
| **Books** | `documents/Books/<Book>/core N.txt` | none (audio URL baked into `BookAudioContent.swift`) | `seed_book_audio.py` (audio only) | `book-media` |

Serving code: `backend/app/api/v1/endpoints/learn.py`, backed by
`services/money_moves_content_service.py` and `services/journey_content_service.py`. Both read
authored content from the DB JSONB and overlay the row's `audio_url` onto `content["audioUrl"]`.
Both defend against malformed rows (one bad article can't collapse the catalog).

Vendored copies (`backend/data/money_moves.json`, `backend/data/journey_lessons.json`) exist
because Railway deploys only `backend/`. Migrations: `065_money_moves_content.sql`,
`061_journey_media_bucket.sql`, `068_book_media_bucket.sql`.

### 4.1 "Publish without an app update" mechanism

Authoritative docs: `.claude/rules/learn-content.md`, `.claude/skills/add-learn-content/SKILL.md`.

1. **Content, audio, and timings all live server-side** — content JSON (incl. embedded read-along
   fields) in the DB JSONB; narration `.m4a` in a **public** Storage bucket; the seed script bakes
   the public `audioUrl` into both the JSONB and a first-class `audio_url` column.
2. **iOS prefers remote over bundled** — the shipped `Resources/*.json` is only an offline
   fallback. A brand-new DB row **appears in the already-installed app**, with audio and synced
   highlighting, **no new build**.
3. **Protecting invariant** — every read-along field (`readAlong`, `itemsReadAlong`,
   `readAlongWords`) is **Optional** in the iOS DTOs and degrades gracefully (plain text →
   audio-no-highlight → full read-along). Nothing is hardcoded in Swift.
4. **Latency** — both content services use `_TTL_SECONDS = 3600` (1-hour in-memory cache, with
   `_inflight` guard + stale-cache fallback). New content surfaces **after the ≤1h cache expires
   or a Railway restart**.

### 4.2 End-to-end workflow: `author → generate → align → seed → wait`

**Learn (Money Moves / Journey)** — run from `backend/`:
1. **Author** — add to the frontend JSON (unique `slug` / `audioClip`). Editing JSON alone
   publishes nothing.
2. **Generate audio** (only quota step) — `generate_money_moves_audio.py <slug>` /
   `generate_journey_audio.py <key>` (or re-voice via `clone_learn_audio.py` on RunPod).
3. **Align** (free, local) — `align_money_moves_audio.py` (sentence) / `align_journey_audio.py`
   (word) → writes timings back into the JSON.
4. **Seed** — `seed_money_moves.py` / `seed_journey.py` → uploads clips, bakes `audioUrl` +
   timings, upserts one idempotent row (reseed is wipe-safe).
5. **Wait** — surfaces after the ≤1h cache expires or a Railway restart.

**Verify:** `pytest tests/test_money_moves_schema_parity.py tests/test_journey_schema_parity.py`
(spans monotonic, in-bounds vs ffprobe duration, word-count parity).

**Books** (GPU-heavy): author `core N.txt` → map ref in `generate_book_audio_clone.py` →
`sync_up.sh` → generate on RunPod → `sync_down.sh` → (optional) `normalize_book_audio.py` →
`seed_book_audio.py --force` → `gen_books_swift.py` → `align_book_audio.py` →
`gen_book_read_along.py` → Xcode rebuild.

---

## 5. iOS visual & UI assets

Dark-theme SwiftUI, Atomic Design (`Views/{Atoms,Molecules,Organisms,Screens}/`). The assets
below are the visually distinctive, screen-recordable / screenshot-able surfaces.

### 5.1 The "orb" — AI voice visualizer

- **`Views/Atoms/AIVoiceOrb.swift`** — an animated Siri-style sphere (default 120pt). A
  cyan→blue→purple→pink gradient (`#06B6D4` / `#3B82F6` / `#8B5CF6` / `#EC4899`) over a dark
  radial core. Layers: outer radial glow halo, three animated sine-wave layers, a white inner
  highlight, and a rotating angular-gradient rim. **Pulses on every spoken word** via
  `.onChange(of: voiceManager.currentWordRange)` → scales to 1.15× and back.
- **Driver:** `Services/AIVoiceManager.swift` (`isPlaying`, `currentWordRange`).
- **Used at:** `Views/Organisms/LessonTopicCardView.swift` (the Investor Journey lesson player).
  Note: the AI **chat** screen (`Views/Screens/AIChatScreen.swift`) does **not** use the orb.
- **Sibling glow visuals:** `Views/Atoms/AudioArtworkThumbnail.swift` → `AudioArtworkLarge`
  (breathing radial-glow artwork on the full-screen player); `Views/Molecules/AudioStatusIsland.swift`
  → `WaveformIndicator` (3-bar animated equalizer).

### 5.2 Chart / data-viz components

- **Flagship price chart:** `Views/Molecules/TickerChartView.swift` + the renderer engine in
  `Views/Molecules/Chart/` (`MainChartCanvas`, `LineChartRenderer`, `AreaChartRenderer`,
  `CandlestickChartRenderer`, `ChartCrosshair`, `ChartGridLines`, …). Time-range selector,
  chart-type switching, technical overlays, interactive crosshair.
- **Sparklines / mini:** `Views/Atoms/TintedSparkline.swift` (Caydex Home primitive),
  `SparklineView.swift`, `MiniStockChart.swift`, `Views/Molecules/PriceActionSparkline.swift`.
- **Analytical:** `ReportMoatRadarChart.swift` (pentagon radar), `GrowthChartView.swift`,
  `ProfitPowerChartView.swift`, `SmartMoneyFlowChart.swift`, `EarningsTimelineChart.swift`,
  `Views/Atoms/DonutChartView.swift`, and many more under `Views/Molecules/`.

### 5.3 Audio player UI

- **Mini player** — `Views/Molecules/GlobalMiniPlayer.swift`: floating capsule (88% width, 60pt),
  dark navy `#1A1F2E` with a glowing blue border, bottom progress bar, mounted globally via
  `Views/Modifiers/GlobalAudioOverlay.swift` (hosted in `Views/Screens/RootContainerView.swift`).
- **Full-screen player** — `Views/Screens/FullScreenAudioPlayer.swift`: artwork-tinted gradient
  background + `AudioArtworkLarge` (280pt, breathing halo, scales on play), custom slider, ±15s.
- **Status island** — `Views/Molecules/AudioStatusIsland.swift`: Dynamic-Island pill with the
  3-bar animated waveform during chat playback.
- Orchestrated by `Services/AudioManager.swift`. (System Now-Playing / Lock Screen integration
  via `MPNowPlayingInfoCenter` + `MPRemoteCommandCenter`.)

### 5.4 Home / signals screens

`Views/Screens/HomeDashboardView.swift` composes four organisms:
- **Market Pulse** (`Views/Organisms/MarketPulseSection.swift`) — index/crypto/commodity tiles
  with `TintedSparkline`.
- **Daily Scanners** (`Views/Organisms/DailyScannersSection.swift`) — swipeable scanner carousel.
- **App-Exclusive Signals** (`Views/Organisms/ExclusiveSignalsSection.swift`) — a glowing premium
  card (`#1B2233`→`#161B29`, "CAYDEX" sparkles badge, blue glow shadow) with expandable signal rows.
- **Trending Themes** (`Views/Molecules/TrendingThemeTile.swift`).

Smart-money detail surfaces: `CongressFlowSummaryCard.swift`, `SmartMoneyFlowChart.swift`,
`Views/Screens/{AllWhalesView,WhaleProfileView,TradeGroupDetailView,SignalTickerDetailView}.swift`,
`Views/Organisms/ReportHiddenMarketSignalsSection.swift`. Full report:
`Views/Screens/{TickerReportView,TickerDetailView}.swift`.

### 5.5 Brand / theme

- **Single source:** `frontend/ios/ios/Theme/AppTheme.swift` (colors via a `Color(hex:)` ext).
- **Backgrounds:** `#171B26` (app), `#1E2330` (card), `#252B3B` (light card).
- **Accents:** blue `#3B82F6`, cyan `#06B6D4`, yellow `#FACC15`; alert orange `#F97316`, purple `#A855F7`.
- **Sentiment:** green `#22C55E`, red `#EF4444`, amber `#F59E0B`.
- **Text:** white / `#9CA3AF` / `#6B7280`.
- **Type:** `AppTypography` — 5-level semantic ladder + a rounded monospaced-digit "Data" tier
  (Robinhood/Webull/Yahoo-style) for financial numerals.
- **Assets** (`Assets.xcassets/`): `CaydexLogo.imageset/CaydexLogo.png` (wordmark),
  `CaydexSlogan.imageset` (slogan graphic), `AppIcon.appiconset` (app icon), `AccentColor.colorset`.

---

## 6. Backend architecture conventions (context)

Relevant when extending any of the above. Full detail in `.claude/rules/` and CLAUDE.md.

- **Four-layer separation:** `api/v1/endpoints/` (HTTP) → `services/` (logic + cache) →
  `integrations/` (thin HTTP clients) → `schemas/` (Pydantic v2).
- **Cache-aside two-tier:** in-memory dict (≈5 min) + Supabase `*_cache` table (≈24 h) +
  `_inflight` dedup. Reference: `services/profit_power_service.py`.
- **Async-first:** handlers are `async def`; parallel FMP via
  `asyncio.gather(..., return_exceptions=True)`.
- **No ORM:** raw Supabase SDK via `get_supabase()`. Migrations in
  `backend/database/migrations/NNN_*.sql` (idempotent; applied manually).
- **Error contract:** every error matches the iOS `APIErrorResponse` shape
  `{error_code, message, user_message, action?, details?}`.

---

## 7. Infrastructure & cost profile

| Resource | What | Cost |
|---|---|---|
| Railway | FastAPI backend host (CPU-only) | Subscription |
| Supabase | Postgres + pgvector + Storage buckets | Subscription/free tier |
| Gemini API | LLM + TTS (Achird) | Per-token; TTS on free-tier throttle |
| FMP `/stable` | Financial data | Premium plan |
| RunPod RTX 4090 | Chatterbox voice cloning (on-demand) | ~$0.34/hr, ~$0.15–0.25/book |
| MMS_FA alignment | Read-along timings | Free (local) |
| Storage buckets | `money-moves-media`, `journey-media`, `book-media` (public); `research-pdfs` (private) | Supabase Storage |

The audio content pipeline is therefore nearly free at the margin: cloud TTS is free-tier
throttled, cloning is a few cents of GPU per long piece, and alignment is fully local.

---

## 8. Identity constraint (applies to all AI-produced content)

`_IDENTITY_RULE` in `backend/app/services/agents/persona_config.py` is prepended to **every**
persona system prompt (and enforced in `services/chat_service.py`). The agent is **"Cay AI, the
intelligent agent powering the Caydex app"** and must **never reveal or hint at the underlying
model/provider** (never say Google, Gemini, OpenAI, GPT, "LLM," "language model," etc.). If asked
who made it: "Cay AI by Caydex."

The backend runs Gemini (`research_agent.py` imports `google.generativeai`; `_MODEL_CHAIN` in
`geopolitical_macro_service.py` references `gemini-2.5-*`), but **this must never surface** in any
produced or quoted content.

Additional compliance note baked into the personas: the report avoids explicit buy/sell calls;
`fair_value` is presented as one objective DCF figure, not advice.

---

## 9. File-path index

**AI pipeline:** `backend/app/services/agents/{research_agent,persona_config,persona_scoring,
card_verdict,fmp_tools,narrative_prompts,ticker_report_data_collector}.py` ·
`backend/app/schemas/ticker_report.py`

**Report store / PDF:** `backend/app/services/{research_service,ticker_report_cache,
ticker_data_cache,pdf_report_service,pdf_charts}.py` · `backend/app/templates/pdf/`

**Data / signals:** `backend/data/whale_registry.json` · `backend/app/services/{whale_service,
_whale_common,signals_service,holders_service,earnings_service,geopolitical_macro_service}.py` ·
`backend/app/api/v1/endpoints/whales.py`

**TTS (Gemini/Achird):** `backend/scripts/{generate_money_moves_audio,generate_journey_audio,
gen_voice_samples}.py`

**TTS (Chatterbox clone):** `backend/scripts/{generate_book_audio_clone,clone_learn_audio,
clone_prototype}.py` · refs in `backend/data/voice_clone/refs/` (house voice
`caydex_voice_achird_v2.wav`) · `backend/scripts/runpod/{README.md,setup_runpod.sh,sync_up.sh,
sync_down.sh,time_and_cost.py}` · `backend/{requirements_clone.txt,Dockerfile}`

**Alignment / normalization:** `backend/scripts/{_forced_align,align_money_moves_audio,
align_journey_audio,align_book_audio,gen_book_read_along,normalize_book_audio,normalize_book_speed}.py`

**Content serving / seeding:** `backend/app/api/v1/endpoints/learn.py` ·
`backend/app/services/{money_moves_content_service,journey_content_service}.py` ·
`backend/scripts/{seed_money_moves,seed_journey,seed_book_audio}.py`

**Content sources:** `frontend/ios/ios/Resources/MoneyMoves/money_moves.json` ·
`frontend/ios/ios/Resources/Journey/journey_lessons.json` · `documents/Books/<Book>/core N.txt`

**iOS visuals:** `frontend/ios/ios/Views/Atoms/AIVoiceOrb.swift` ·
`Views/Atoms/AudioArtworkThumbnail.swift` · `Views/Molecules/TickerChartView.swift` +
`Views/Molecules/Chart/` · `Views/Molecules/GlobalMiniPlayer.swift` ·
`Views/Screens/FullScreenAudioPlayer.swift` · `Views/Screens/HomeDashboardView.swift` ·
`frontend/ios/ios/Theme/AppTheme.swift` · `frontend/ios/ios/Assets.xcassets/`

**Docs / playbooks:** `.claude/rules/learn-content.md` ·
`.claude/skills/add-learn-content/SKILL.md` · `.claude/skills/add-research-persona/`
