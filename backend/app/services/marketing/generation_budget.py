"""
The per-generation budget the script lease arithmetic is built on (`script_service`,
SYSTEM_DESIGN_GUIDELINES §12.5).

Two numbers, kept here — not in `script_service` or `writer_service` — because BOTH sides must
agree on them and neither may import the other at module load (`writer_service` loads the
agents package, and through it the FMP client; `script_service` loads the ledger):

* `MODEL_CALLS_PER_GENERATION` — the most model calls ONE generation makes. The writer must
  never exceed it; the script service sizes how long a live generation task may run from it.
* `LEDGER_STATEMENT_SECONDS` — how long ONE PostgREST statement can block before the client
  gives up. Every ledger round trip a generation makes is bounded only by this.

Pure: stdlib plus the postgrest library's own constant. No Supabase client, no Gemini, no FMP
(`tests/test_marketing_import_boundary.py` scans every module in this package).
"""

from __future__ import annotations

from postgrest.constants import DEFAULT_POSTGREST_CLIENT_TIMEOUT

#: The most `generate_json` calls ONE `writer_service.generate_package` makes:
#: draft → judge (of the draft) → ONE repair → judge (of the repair) = 4. Each of them is
#: preceded by one `before_call` (the script service refreshes its lease there), so a
#: generation also makes at most this many lease refreshes.
#:
#: ⚠️ CONTRACT with `writer_service.generate_package` (and `judge.py`): a generation must make
#: AT MOST this many model calls, whatever the drafts, verdicts or errors. `script_service`
#: sizes OWNER_ALIVE_SECONDS from it — a task running longer than that is presumed wedged, and
#: at a cap the day is closed under it, fencing out the paid package it was about to write. A
#: writer change that adds a call (a second repair, a retry of the judge, a "polish" pass) must
#: raise this number in the same change; one that ignores it reintroduces exactly that loss. The
#: per-run model-call bound is (MAX_GENERATIONS + MAX_WRITER_FAILURES - 1) × this
#: (`script_service.MAX_MODEL_CALLS_PER_RUN`).
MODEL_CALLS_PER_GENERATION = 4

#: The client timeout of ONE PostgREST statement, in seconds. `app/database.py` builds the
#: service-role client with supabase-py's defaults (`ClientOptions.postgrest_client_timeout`
#: = postgrest's DEFAULT_POSTGREST_CLIENT_TIMEOUT, 120 s) and `_force_http1_on_postgrest` copies
#: that timeout onto its HTTP/1.1 session; `sb_exec` adds no timeout of its own. Read from the
#: library rather than mirrored, so a supabase-py/postgrest bump moves the bound with it
#: (`tests/test_marketing_residuals_lease.py` pins that the live client uses exactly this).
#:
#: Caveat, stated rather than hidden: httpx applies it PER PHASE (connect, each write, each read
#: gap, pool wait), and `asyncio.to_thread` can queue behind a saturated default executor. A
#: small PostgREST answer is one read, so this is the practical per-statement bound — not a
#: wall-clock guarantee.
LEDGER_STATEMENT_SECONDS: float = float(DEFAULT_POSTGREST_CLIENT_TIMEOUT)
